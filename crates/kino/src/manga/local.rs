//! The user's own manga: .cbz archives and folders of images in one folder they
//! choose. A series is a sub-folder (its chapters are .cbz files or folders of
//! images inside), or a single .cbz at the top. Volumes downloaded by torrent go
//! there too.

use std::cmp::Ordering;
use std::io::Read;
use std::path::{Path, PathBuf};

use serde_json::{Value, json};
use sha2::{Digest, Sha256};

use super::MangaError;
use crate::db::Db;

pub const FOLDER_KEY: &str = "manga_folder_v1";
const IMAGES: [&str; 6] = ["jpg", "jpeg", "png", "webp", "gif", "avif"];

#[derive(PartialEq, Eq, Debug)]
pub enum Part {
    Number(String),
    Text(String),
}

impl Ord for Part {
    fn cmp(&self, other: &Self) -> Ordering {
        match (self, other) {
            (Part::Number(a), Part::Number(b)) => {
                let (a, b) = (a.trim_start_matches('0'), b.trim_start_matches('0'));
                a.len().cmp(&b.len()).then_with(|| a.cmp(b))
            }
            (Part::Text(a), Part::Text(b)) => a.cmp(b),
            (Part::Number(_), Part::Text(_)) => Ordering::Less,
            (Part::Text(_), Part::Number(_)) => Ordering::Greater,
        }
    }
}

impl PartialOrd for Part {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

/// « p2 » before « p10 », whatever the case.
pub fn natural(text: &str) -> Vec<Part> {
    let mut parts = Vec::new();
    let mut current = String::new();
    let mut digits = false;
    for c in text.chars() {
        if c.is_ascii_digit() != digits && !current.is_empty() {
            parts.push(if digits {
                Part::Number(std::mem::take(&mut current))
            } else {
                Part::Text(std::mem::take(&mut current))
            });
        }
        digits = c.is_ascii_digit();
        current.extend(if digits {
            vec![c]
        } else {
            c.to_lowercase().collect()
        });
    }
    if !current.is_empty() {
        parts.push(if digits {
            Part::Number(current)
        } else {
            Part::Text(current)
        });
    }
    parts
}

fn name_of(path: &Path) -> String {
    path.file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_default()
}

fn stem_of(path: &Path) -> String {
    path.file_stem()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_default()
}

fn extension(name: &str) -> String {
    Path::new(name)
        .extension()
        .map(|e| e.to_string_lossy().to_lowercase())
        .unwrap_or_default()
}

fn is_cbz(path: &Path) -> bool {
    path.is_file() && extension(&name_of(path)) == "cbz"
}

fn is_image(name: &str) -> bool {
    IMAGES.contains(&extension(name).as_str())
}

fn sorted_children(dir: &Path) -> Vec<PathBuf> {
    let mut children: Vec<PathBuf> = std::fs::read_dir(dir)
        .map(|entries| entries.filter_map(|e| e.ok().map(|e| e.path())).collect())
        .unwrap_or_default();
    children.sort_by_key(|a| natural(&name_of(a)));
    children
}

fn is_chapter(path: &Path) -> bool {
    is_cbz(path)
        || (path.is_dir()
            && sorted_children(path)
                .iter()
                .any(|p| p.is_file() && is_image(&name_of(p))))
}

pub fn key(relative: &Path) -> String {
    hex::encode(Sha256::digest(relative.to_string_lossy().as_bytes()))[..20].to_string()
}

pub fn folder(db: &Db) -> Option<PathBuf> {
    let path = PathBuf::from(db.get_config(FOLDER_KEY)?.as_str()?);
    path.is_dir().then_some(path)
}

fn expand(path: &str) -> PathBuf {
    match path.strip_prefix("~") {
        Some(rest) if rest.is_empty() || rest.starts_with('/') => {
            crate::paths::home().join(rest.trim_start_matches('/'))
        }
        _ => PathBuf::from(path),
    }
}

pub fn set_folder(db: &Db, path: &str) -> Result<Value, MangaError> {
    let path = expand(path);
    if !path.is_dir() {
        return Err(MangaError::refused("Ce dossier n'existe pas."));
    }
    let resolved = std::fs::canonicalize(&path).unwrap_or(path);
    db.save_config(FOLDER_KEY, &json!(resolved.to_string_lossy()))
        .map_err(|e| MangaError::down(&e.to_string()))?;
    Ok(series(db))
}

/// (series key, series path, chapter paths) of the chosen folder.
fn entries(root: &Path) -> Vec<(String, PathBuf, Vec<PathBuf>)> {
    let mut found = Vec::new();
    for item in sorted_children(root) {
        let name = name_of(&item);
        if name.starts_with('.') {
            continue;
        }
        let relative = item.strip_prefix(root).unwrap_or(&item).to_path_buf();
        if is_cbz(&item) {
            found.push((key(&relative), item.clone(), vec![item]));
        } else if item.is_dir() {
            let inside: Vec<PathBuf> = sorted_children(&item)
                .into_iter()
                .filter(|p| !name_of(p).starts_with('.') && is_chapter(p))
                .collect();
            if !inside.is_empty() {
                found.push((key(&relative), item, inside));
            } else if is_chapter(&item) {
                found.push((key(&relative), item.clone(), vec![item]));
            }
        }
    }
    found
}

fn title(path: &Path) -> String {
    if path.is_file() {
        stem_of(path)
    } else {
        name_of(path)
    }
}

pub fn series(db: &Db) -> Value {
    let Some(root) = folder(db) else {
        return json!({"folder": "", "series": []});
    };
    let list: Vec<Value> = entries(&root)
        .into_iter()
        .map(|(key, path, chapters)| {
            json!({"id": key, "source": "local", "title": title(&path), "chapters": chapters.len(),
                   "cover": format!("/api/manga/local/page?series={key}&chapter=0&page=0")})
        })
        .collect();
    json!({"folder": root.to_string_lossy(), "series": list})
}

fn find(db: &Db, series_key: &str) -> Result<(PathBuf, Vec<PathBuf>), MangaError> {
    folder(db)
        .and_then(|root| entries(&root).into_iter().find(|(k, _, _)| k == series_key))
        .map(|(_, path, chapters)| (path, chapters))
        .ok_or_else(|| MangaError::refused("Série inconnue dans votre dossier."))
}

fn page_names(chapter: &Path) -> Vec<String> {
    let mut names: Vec<String> = if chapter.is_file() {
        std::fs::File::open(chapter)
            .ok()
            .and_then(|f| zip::ZipArchive::new(f).ok())
            .map(|archive| {
                archive
                    .file_names()
                    .filter(|n| is_image(n) && !n.starts_with("__MACOSX"))
                    .map(str::to_string)
                    .collect()
            })
            .unwrap_or_default()
    } else {
        sorted_children(chapter)
            .iter()
            .filter(|p| p.is_file() && is_image(&name_of(p)))
            .map(|p| name_of(p))
            .collect()
    };
    names.sort_by_key(|a| natural(a));
    names
}

pub fn chapters(db: &Db, series_key: &str) -> Result<Value, MangaError> {
    let (path, found) = find(db, series_key)?;
    let list: Vec<Value> = found
        .iter()
        .enumerate()
        .map(|(i, chapter)| json!({"id": i.to_string(), "number": (i + 1).to_string(), "title": title(chapter), "pages": page_names(chapter).len()}))
        .collect();
    Ok(json!({"title": title(&path), "chapters": list}))
}

/// (bytes, content type) of one page; only from the chosen folder.
pub fn page(
    db: &Db,
    series_key: &str,
    chapter: &str,
    page: &str,
) -> Result<(Vec<u8>, &'static str), MangaError> {
    let unknown = || MangaError::refused("Page inconnue.");
    let (_, found) = find(db, series_key)?;
    let chapter = found
        .get(chapter.parse::<usize>().map_err(|_| unknown())?)
        .ok_or_else(unknown)?;
    let names = page_names(chapter);
    let name = names
        .get(page.parse::<usize>().map_err(|_| unknown())?)
        .ok_or_else(unknown)?;
    let kind = match extension(name).as_str() {
        "png" => "image/png",
        "webp" => "image/webp",
        "gif" => "image/gif",
        "avif" => "image/avif",
        _ => "image/jpeg",
    };
    let data = if chapter.is_file() {
        let file = std::fs::File::open(chapter).map_err(|_| unknown())?;
        let mut archive = zip::ZipArchive::new(file).map_err(|_| unknown())?;
        let mut entry = archive.by_name(name).map_err(|_| unknown())?;
        let mut data = Vec::new();
        entry.read_to_end(&mut data).map_err(|_| unknown())?;
        data
    } else {
        std::fs::read(chapter.join(name)).map_err(|_| unknown())?
    };
    Ok((data, kind))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    fn image(color: u8) -> Vec<u8> {
        let mut data = vec![137, 80, 78, 71];
        data.extend(std::iter::repeat_n(color, 20));
        data
    }

    fn cbz(path: &Path, files: &[(&str, Vec<u8>)]) {
        let mut writer = zip::ZipWriter::new(std::fs::File::create(path).unwrap());
        for (name, data) in files {
            writer
                .start_file(*name, zip::write::SimpleFileOptions::default())
                .unwrap();
            writer.write_all(data).unwrap();
        }
        writer.finish().unwrap();
    }

    #[test]
    fn series_chapters_and_pages_from_the_chosen_folder() {
        let dir = tempfile::tempdir().unwrap();
        let db = Db::open(&dir.path().join("kino.db")).unwrap();
        let root = dir.path().join("Mangas");
        std::fs::create_dir_all(root.join("One Piece/Tome 1")).unwrap();
        cbz(
            &root.join("One Piece/Tome 2.cbz"),
            &[
                ("p10.png", image(2)),
                ("p2.png", image(1)),
                ("__MACOSX/p1.png", vec![1]),
                ("notes.txt", vec![1]),
            ],
        );
        std::fs::write(root.join("One Piece/Tome 1/001.jpg"), image(5)).unwrap();
        cbz(&root.join("Oneshot.cbz"), &[("a.png", image(9))]);
        std::fs::create_dir_all(root.join("Vide")).unwrap();
        std::fs::create_dir_all(root.join(".cache")).unwrap();

        assert_eq!(series(&db), json!({"folder": "", "series": []}));
        assert!(set_folder(&db, dir.path().join("nowhere").to_str().unwrap()).is_err());
        let listed = set_folder(&db, root.to_str().unwrap()).unwrap();
        let names: Vec<(String, u64)> = listed["series"]
            .as_array()
            .unwrap()
            .iter()
            .map(|s| {
                (
                    s["title"].as_str().unwrap().to_string(),
                    s["chapters"].as_u64().unwrap(),
                )
            })
            .collect();
        assert_eq!(
            names,
            vec![("One Piece".into(), 2), ("Oneshot".into(), 1)],
            "an empty folder and a hidden one are left out"
        );
        let key = listed["series"][0]["id"].as_str().unwrap().to_string();
        assert_eq!(
            key,
            super::key(Path::new("One Piece")),
            "the same key as the Python version"
        );
        let list = chapters(&db, &key).unwrap();
        assert_eq!(list["chapters"][0]["title"], "Tome 1");
        assert_eq!(list["chapters"][1]["pages"], 2);
        assert_eq!(
            page(&db, &key, "1", "0").unwrap(),
            (image(1), "image/png"),
            "p2 before p10"
        );
        assert_eq!(page(&db, &key, "0", "0").unwrap(), (image(5), "image/jpeg"));
        for (s, c, p) in [
            (key.as_str(), "5", "0"),
            (key.as_str(), "1", "9"),
            ("00000000000000000000", "0", "0"),
            ("../..", "0", "0"),
            (key.as_str(), "x", "0"),
        ] {
            assert!(page(&db, s, c, p).is_err(), "{s} {c} {p}");
        }
    }

    #[test]
    fn natural_order() {
        let mut names = vec!["p10", "P2", "p1", "a"];
        names.sort_by_key(|a| natural(a));
        assert_eq!(names, vec!["a", "p1", "P2", "p10"]);
    }
}
