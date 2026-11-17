//! Whole manga volumes by torrent, kept in the user's manga folder. A volume
//! pack found on Nyaa is opened peer to peer, after the same consent as
//! films; KINO lists its comic archives, then fetches only the volume chosen.
//! Once complete the archive is copied into <folder>/<series>/<file>.cbz: one
//! of the user's own files, read offline by the local reader.

use std::collections::{HashMap, HashSet};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::sync::{LazyLock, Mutex};

use serde_json::{Value, json};

use super::MangaError;
use super::local;
use crate::db::Db;
use crate::p2p::P2p;

const ARCHIVES: [&str; 2] = [".cbz", ".zip"];

#[derive(Clone)]
struct Job {
    series: String,
    name: String,
    size: u64,
    path: String,
    error: String,
}

/// (info hash, file index) → the volume being fetched.
static JOBS: LazyLock<Mutex<HashMap<(String, usize), Job>>> = LazyLock::new(Default::default);

fn jobs() -> std::sync::MutexGuard<'static, HashMap<(String, usize), Job>> {
    JOBS.lock().unwrap_or_else(|e| e.into_inner())
}

fn hash_of(value: &str) -> Result<String, MangaError> {
    let value = value.to_lowercase();
    (value.len() == 40 && value.chars().all(|c| c.is_ascii_hexdigit()))
        .then_some(value)
        .ok_or_else(|| MangaError::refused("Torrent inconnu."))
}

fn is_archive(name: &str) -> bool {
    let lower = name.to_lowercase();
    ARCHIVES.iter().any(|a| lower.ends_with(a))
}

fn last_part(path: &str) -> String {
    path.rsplit('/').next().unwrap_or(path).to_string()
}

/// The torrent's files, the consent checked; an unanswered torrent is a message.
async fn files_of(
    p2p: &P2p,
    hash: &str,
    name: &str,
) -> Result<Vec<(usize, String, u64)>, MangaError> {
    if p2p.consent().is_none() {
        return Err(MangaError::Consent);
    }
    let magnet = super::sources::magnet(hash, name);
    match p2p.files(hash, &magnet).await {
        Ok(Some(files)) => Ok(files),
        Ok(None) => Err(MangaError::refused(
            "Ce torrent ne répond pas : aucun pair pour l'instant.",
        )),
        Err(e) => Err(MangaError::refused(&e)),
    }
}

/// The comic archives of a torrent: [{index, name, size}], in volume order.
pub async fn files(p2p: &P2p, hash: &str, name: &str) -> Result<Value, MangaError> {
    let hash = hash_of(hash)?;
    let mut found: Vec<Value> = files_of(p2p, &hash, name)
        .await?
        .into_iter()
        .filter(|(_, path, _)| is_archive(path))
        .map(|(index, path, size)| json!({"index": index, "name": last_part(&path), "size": size}))
        .collect();
    found.sort_by_key(|f| local::natural(f["name"].as_str().unwrap_or("")));
    Ok(json!({"files": found}))
}

fn safe(name: &str) -> String {
    static UNSAFE: LazyLock<regex::Regex> =
        LazyLock::new(|| regex::Regex::new(r#"[\\/:*?"<>|]+"#).unwrap());
    let cleaned: String = UNSAFE
        .replace_all(name, " ")
        .trim()
        .chars()
        .take(120)
        .collect();
    if cleaned.is_empty() {
        "Manga".into()
    } else {
        cleaned
    }
}

/// Fetches one archive of the torrent, before the others still wanted.
pub async fn start(
    p2p: &P2p,
    db: &Db,
    hash: &str,
    index: &Value,
    series: &str,
) -> Result<Value, MangaError> {
    let hash = hash_of(hash)?;
    let files = files_of(p2p, &hash, "").await?;
    let index = index
        .as_i64()
        .or_else(|| crate::play::text(index).trim().parse().ok())
        .unwrap_or(-1);
    let Some((index, path, size)) = usize::try_from(index)
        .ok()
        .and_then(|i| files.iter().find(|f| f.0 == i))
        .filter(|f| is_archive(&f.1))
        .cloned()
    else {
        return Err(MangaError::refused("Volume inconnu dans ce torrent."));
    };
    let wanted: HashSet<usize> = {
        let mut jobs = jobs();
        jobs.entry((hash.clone(), index)).or_insert_with(|| Job {
            series: safe(series),
            name: last_part(&path),
            size,
            path: String::new(),
            error: String::new(),
        });
        jobs.iter()
            .filter(|((h, _), job)| *h == hash && job.path.is_empty())
            .map(|((_, i), _)| *i)
            .collect()
    };
    p2p.fetch(&hash, &wanted)
        .await
        .map_err(|e| MangaError::refused(&e))?;
    status(p2p, db, &hash, &json!(index)).await
}

/// {progress 0-1, done, error, name}; a finished volume is copied into the manga folder once.
pub async fn status(p2p: &P2p, db: &Db, hash: &str, index: &Value) -> Result<Value, MangaError> {
    let hash = hash_of(hash)?;
    let index = index
        .as_u64()
        .or_else(|| crate::play::text(index).trim().parse().ok())
        .ok_or_else(|| MangaError::refused("Volume inconnu dans ce torrent."))?
        as usize;
    let key = (hash.clone(), index);
    let Some(job) = jobs().get(&key).cloned() else {
        return Err(MangaError::refused("Aucun téléchargement de ce volume."));
    };
    if !job.path.is_empty() || !job.error.is_empty() {
        return Ok(answer(
            db,
            &job,
            if job.path.is_empty() { 0.0 } else { 1.0 },
        ));
    }
    let Some((done, source)) = p2p.progress(&hash, index).await else {
        let mut jobs = jobs();
        let job = jobs.get_mut(&key).map(|j| {
            j.error = "Le téléchargement s'est arrêté.".into();
            j.clone()
        });
        return Ok(job.map_or(json!({}), |j| answer(db, &j, 0.0)));
    };
    let progress = if job.size > 0 {
        done as f64 / job.size as f64
    } else {
        0.0
    };
    if done >= job.size
        && let Some(path) = finish(db, &job, &source).await
        && let Some(j) = jobs().get_mut(&key)
    {
        j.path = path;
    }
    let job = jobs().get(&key).cloned().unwrap_or(job);
    Ok(answer(db, &job, progress.min(1.0)))
}

/// The manga folder, made in Documents the first time.
fn folder(db: &Db) -> Option<PathBuf> {
    if let Some(current) = local::folder(db) {
        return Some(current);
    }
    let default = crate::paths::home().join("Documents").join("KINO Manga");
    std::fs::create_dir_all(&default).ok()?;
    db.save_config(local::FOLDER_KEY, &json!(default.to_string_lossy()))
        .ok()?;
    Some(default)
}

/// Copies the archive once it reads as a whole (the last pieces may still be landing).
async fn finish(db: &Db, job: &Job, source: &Path) -> Option<String> {
    let target = folder(db)?.join(&job.series).join(&job.name);
    std::fs::create_dir_all(target.parent()?).ok()?;
    for _ in 0..20 {
        let whole = std::fs::File::open(source)
            .ok()
            .and_then(|f| zip::ZipArchive::new(f).ok())
            .is_some_and(|mut archive| {
                (0..archive.len()).all(|i| {
                    archive.by_index(i).is_ok_and(|mut entry| {
                        std::io::copy(&mut entry.by_ref(), &mut std::io::sink()).is_ok()
                    })
                })
            });
        if whole && std::fs::copy(source, &target).is_ok() {
            return Some(target.display().to_string());
        }
        tokio::time::sleep(std::time::Duration::from_millis(250)).await;
    }
    None
}

fn answer(db: &Db, job: &Job, progress: f64) -> Value {
    let mut local_key = String::new();
    if !job.path.is_empty()
        && let Some(root) = local::folder(db)
        && let Some(parent) = Path::new(&job.path).parent()
        && let Ok(relative) = parent.strip_prefix(&root)
    {
        local_key = local::key(relative);
    }
    json!({"name": job.name, "progress": crate::sources::stream::py_round(progress, 4), "done": !job.path.is_empty(), "error": job.error,
           "series": job.series, "local": local_key})
}
