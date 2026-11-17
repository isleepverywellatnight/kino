//! A magnet's debrid link: one path per service. {"ready": true, "files": [...]}
//! once the files can be read, {"ready": false, "torrent_id", "progress"...}
//! while the service downloads it. A resolve link (Torrentio, Comet) is tried first.

use std::collections::HashMap;
use std::sync::{LazyLock, Mutex};
use std::time::Duration;

use regex::{Regex, RegexBuilder};
use reqwest::Method;
use serde_json::{Value, json};

use crate::debrid::PROVIDERS;
use crate::net::{Net, NetError, NetResult};

pub const VIDEO_EXTENSIONS: [&str; 8] = [
    ".mkv", ".mp4", ".avi", ".m4v", ".mov", ".ts", ".wmv", ".webm",
];
const RD: &str = "https://api.real-debrid.com/rest/1.0";
const MB25: f64 = 25.0 * 1024.0 * 1024.0;

static COMPILED: LazyLock<Mutex<HashMap<(String, bool), Regex>>> =
    LazyLock::new(|| Mutex::new(HashMap::new()));

/// A regular expression built once (the episode patterns depend on its numbers).
fn compiled(pattern: &str, insensitive: bool) -> Regex {
    let mut cache = COMPILED.lock().unwrap_or_else(|e| e.into_inner());
    if cache.len() > 4096 {
        cache.clear();
    }
    cache
        .entry((pattern.to_string(), insensitive))
        .or_insert_with(|| {
            RegexBuilder::new(pattern)
                .case_insensitive(insensitive)
                .build()
                .expect(pattern)
        })
        .clone()
}

fn ci(pattern: &str) -> Regex {
    compiled(pattern, true)
}

fn is_video(name: &str) -> bool {
    let lower = name.to_lowercase();
    VIDEO_EXTENSIONS.iter().any(|e| lower.ends_with(e))
}

pub fn format_size(bytes: f64) -> String {
    if bytes == 0.0 {
        return "0 B".into();
    }
    let mut value = bytes;
    for unit in ["B", "KB", "MB", "GB", "TB"] {
        if value < 1024.0 {
            return format!("{value:.2} {unit}");
        }
        value /= 1024.0;
    }
    format!("{value:.2} PB")
}

fn number(value: &Value) -> f64 {
    value
        .as_f64()
        .or_else(|| value.as_str().and_then(|s| s.trim().parse().ok()))
        .unwrap_or(0.0)
}

/// Whether a file of a pack is the episode asked for (anime numbering included).
pub fn is_target_episode(
    filename: &str,
    season: Option<i64>,
    episode: Option<i64>,
    absolute: Option<i64>,
) -> bool {
    if filename.is_empty() || (season.is_none() && episode.is_none() && absolute.is_none()) {
        return true;
    }
    let name = filename.trim();
    let s = season.filter(|s| *s != 0).unwrap_or(1);
    let e = episode.filter(|e| *e != 0).unwrap_or(1);
    let abs = absolute.filter(|a| *a != 0);
    if ci(r"[\s\-_\[(](?:NC)?(?:OP|ED|PV|TRAILER|SAMPLE|PREVIEW|MENU)\b(?:\d+)?").is_match(name)
        && (!ci(r"\bOne\s+Piece\b").is_match(name)
            || ci(r"[\s\-_\[(](?:NCOP|NCED|PV|SAMPLE)\b").is_match(name))
    {
        return false;
    }
    if ci(&format!(r"(?:s0?{s}[\.\-_ ]?e0?{e}\b|\b{s}x0?{e}\b)")).is_match(name) {
        return true;
    }
    let marker = format!(r"(?:s0?{s}|season\s*0?{s}|{s}(?:nd|rd|th|st)\s*season|part\s*0?{s})\b");
    if ci(&marker).is_match(name)
        && ci(&format!(
            r"{marker}[\s\-_:]*(?:ep(?:isode)?\.?\s*|e)?0*{e}(?:v\d+)?(?:[\s\-_\]\).]|$)"
        ))
        .is_match(name)
    {
        return true;
    }
    let mut cleaned = compiled(r"\[[0-9a-fA-F]{6,8}\]", false)
        .replace_all(name, "")
        .into_owned();
    for (pattern, insensitive) in [
        (r"\b(?:2160p|1080p|720p|480p|4k|uhd|fhd|hd)\b", true),
        (r"\b(?:x264|x265|h264|h265|hevc|av1|10bit|8bit)\b", true),
        (r"\b(?:5\.1|7\.1|2\.0)\b", false),
        (r"\b(?:19|20)\d{2}\b", false),
        (r"\b(?:aac|flac|ac3|eac3|dts|mp3)\b", true),
    ] {
        let re = compiled(pattern, insensitive);
        cleaned = re.replace_all(&cleaned, "").into_owned();
    }
    if let Some(c) = ci(r"\b(?:s|season|part)\s*0?(\d+)\b").captures(&cleaned)
        && c[1].parse::<i64>().ok() != Some(s)
    {
        return false;
    }
    let mut targets = vec![e];
    if let Some(a) = abs.filter(|a| *a != e) {
        targets.push(a);
    }
    targets.iter().any(|n| {
        [
            format!(r"[\s\-_]0*{n}(?:v\d+)?[\s\-_\.\]\)]"),
            format!(r"\[0*{n}(?:v\d+)?\]"),
            format!(r"\(0*{n}(?:v\d+)?\)"),
            format!(r"\b(?:ep(?:isode)?\.?|e|#)\s*0*{n}(?:v\d+)?\b"),
        ]
        .iter()
        .any(|p| ci(p).is_match(&cleaned))
    })
}

fn target(name: &str, season: Option<i64>, episode: Option<i64>) -> bool {
    (season.is_some() || episode.is_some()) && is_target_episode(name, season, episode, None)
}

fn file_entry(
    name: &str,
    size: f64,
    download: &Value,
    season: Option<i64>,
    episode: Option<i64>,
) -> Value {
    json!({"filename": name, "filesize": format_size(size), "filesize_bytes": size as i64, "download": download,
           "is_target_ep": target(name, season, episode)})
}

fn sort_files(files: &mut [Value]) {
    files.sort_by(|a, b| {
        let key = |x: &Value| {
            (
                x["is_target_ep"].as_bool() != Some(true),
                x["filename"].as_str().unwrap_or("").to_string(),
            )
        };
        key(a).cmp(&key(b))
    });
}

fn ready(torrent_id: Value, mut files: Vec<Value>) -> Value {
    sort_files(&mut files);
    json!({"ready": true, "torrent_id": torrent_id, "status": "downloaded", "progress": 100, "files": files})
}

fn not_ready(
    torrent_id: Value,
    status: Value,
    progress: Value,
    speed: f64,
    seeders: Value,
) -> Value {
    json!({"ready": false, "torrent_id": torrent_id, "status": status, "progress": progress, "speed": format!("{}/s", format_size(speed)),
           "seeders": seeders, "files": []})
}

fn error(text: impl Into<String>) -> NetError {
    NetError(text.into())
}

/// Where the services answer; tests point them at a local server.
#[derive(Clone)]
pub struct Hosts {
    pub realdebrid: String,
    pub alldebrid: String,
    pub torbox: String,
    pub premiumize: String,
    pub debridlink: String,
    pub megadebrid: String,
}

impl Default for Hosts {
    fn default() -> Self {
        Self {
            realdebrid: RD.into(),
            alldebrid: "https://api.alldebrid.com/v4".into(),
            torbox: "https://api.torbox.app/v1/api".into(),
            premiumize: "https://www.premiumize.me/api".into(),
            debridlink: "https://debrid-link.com/api/v2".into(),
            megadebrid: "https://www.mega-debrid.eu/index.php".into(),
        }
    }
}

pub struct Resolver<'a> {
    pub net: &'a Net,
    pub provider: String,
    pub token: String,
    pub hosts: Hosts,
}

const T: Duration = Duration::from_secs(20);

impl Resolver<'_> {
    fn bearer(&self) -> String {
        format!("Bearer {}", self.token)
    }

    async fn get(&self, url: &str, auth: bool) -> NetResult<Value> {
        let bearer = self.bearer();
        let headers: Vec<(&str, &str)> = if auth {
            vec![("Authorization", bearer.as_str())]
        } else {
            vec![]
        };
        self.net
            .request_json(Method::GET, url, &headers, None, T, 3)
            .await
    }

    async fn post(&self, url: &str, form: &[(&str, &str)], auth: bool) -> NetResult<Value> {
        let bearer = self.bearer();
        let headers: Vec<(&str, &str)> = if auth {
            vec![("Authorization", bearer.as_str())]
        } else {
            vec![]
        };
        self.net.request_form(url, &headers, form, T, 3).await
    }

    fn query(&self, pairs: &[(&str, &str)]) -> String {
        form_urlencoded::Serializer::new(String::new())
            .extend_pairs(pairs.iter())
            .finish()
    }

    /// A Torrentio or Comet link that answers with the file itself when the service has it.
    async fn resolve_link(&self, url: &str) -> NetResult<Value> {
        let response = self
            .net
            .client
            .get(url)
            .header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")
            .header("Range", "bytes=0-0")
            .timeout(Duration::from_secs(25))
            .send()
            .await?;
        let final_url = response.url().to_string();
        let headers = response.headers();
        let range = headers
            .get("content-range")
            .and_then(|v| v.to_str().ok())
            .unwrap_or("");
        let mut total: f64 = range
            .rsplit('/')
            .next()
            .filter(|_| range.contains('/'))
            .and_then(|t| t.parse().ok())
            .unwrap_or(0.0);
        if total == 0.0 {
            total = headers
                .get("content-length")
                .and_then(|v| v.to_str().ok())
                .and_then(|v| v.parse().ok())
                .unwrap_or(0.0);
        }
        drop(response);
        let lower = final_url.to_lowercase();
        if final_url.contains("torrentio.strem.fun")
            || [
                "downloading.mp4",
                "cached.mp4",
                "error.mp4",
                "non_debrid.mp4",
            ]
            .iter()
            .any(|p| lower.ends_with(p))
        {
            return Err(error(
                "Flux non disponible en cache instantané sur votre débrideur.",
            ));
        }
        let badge = PROVIDERS
            .iter()
            .find(|p| p.0 == self.provider)
            .map_or("RD+", |p| p.3);
        let last = final_url
            .rsplit('/')
            .next()
            .unwrap_or("")
            .split('?')
            .next()
            .unwrap_or("");
        let filename = form_urlencoded::parse(format!("x={}", last.replace('+', "%2B")).as_bytes())
            .next()
            .map(|(_, v)| v.into_owned())
            .unwrap_or_default();
        let size = if total > 1024.0 {
            format_size(total)
        } else {
            format!("Prêt (Flux Direct {badge})")
        };
        Ok(
            json!({"ready": true, "torrent_id": "instant_cache", "status": "downloaded", "progress": 100,
                  "files": [{"filename": filename, "filesize": size, "filesize_bytes": total as i64, "download": final_url, "is_target_ep": true}]}),
        )
    }

    pub async fn magnet(
        &self,
        magnet: &str,
        season: Option<i64>,
        episode: Option<i64>,
        resolve_url: Option<&str>,
    ) -> NetResult<Value> {
        let name = PROVIDERS
            .iter()
            .find(|p| p.0 == self.provider)
            .map_or("Real-Debrid", |p| p.1);
        if self.token.is_empty() {
            return Err(error(format!(
                "Clé API {name} manquante. Cliquez sur 'Config' en haut à droite."
            )));
        }
        let link = resolve_url
            .filter(|u| !u.is_empty())
            .or(magnet.starts_with("http").then_some(magnet));
        if let Some(link) = link {
            match self.resolve_link(link).await {
                Ok(found) => return Ok(found),
                Err(e) if !magnet.starts_with("magnet:") => return Err(e),
                Err(_) => {}
            }
        }
        match self.provider.as_str() {
            "alldebrid" => self.alldebrid(magnet, season, episode).await,
            "debridlink" => self.debridlink(magnet, season, episode).await,
            "premiumize" => self.premiumize(magnet, season, episode).await,
            "torbox" => self.torbox(magnet, season, episode).await,
            "megadebrid" => self.megadebrid(magnet).await,
            _ => self.realdebrid(magnet, season, episode).await,
        }
    }

    async fn alldebrid_unlock(
        &self,
        info: &Value,
        season: Option<i64>,
        episode: Option<i64>,
    ) -> NetResult<Value> {
        let links = info["links"].as_array().cloned().unwrap_or_default();
        let mut candidates: Vec<(String, f64, String, bool)> = Vec::new();
        for item in &links {
            let name = item["filename"]
                .as_str()
                .filter(|n| !n.is_empty())
                .unwrap_or("video.mkv")
                .to_string();
            let size = number(&item["size"]);
            let Some(raw) = item["link"].as_str().filter(|l| !l.is_empty()) else {
                continue;
            };
            if !(is_video(&name) || size > MB25) && links.len() > 1 {
                continue;
            }
            let is_target = target(&name, season, episode);
            candidates.push((name, size, raw.to_string(), is_target));
        }
        candidates.sort_by(|a, b| (!a.3, a.0.clone()).cmp(&(!b.3, b.0.clone())));
        let mut files = Vec::new();
        for (name, size, raw, is_target) in candidates.into_iter().take(12) {
            let url = format!(
                "{}/link/unlock?{}",
                self.hosts.alldebrid,
                self.query(&[("agent", "KINO"), ("apikey", &self.token), ("link", &raw)])
            );
            if let Ok(u) = self.get(&url, false).await
                && let Some(link) = u["data"]["link"].as_str().filter(|l| !l.is_empty())
            {
                files.push(json!({"filename": name, "filesize": format_size(size), "filesize_bytes": size as i64, "download": link, "is_target_ep": is_target}));
            }
        }
        Ok(
            json!({"ready": true, "torrent_id": format!("ad:{}", info["id"]), "status": "downloaded", "progress": 100, "files": files}),
        )
    }

    fn alldebrid_info(status: &Value) -> Value {
        match &status["data"]["magnets"] {
            Value::Array(list) => list.first().cloned().unwrap_or(json!({})),
            Value::Object(o) => Value::Object(o.clone()),
            _ => json!({}),
        }
    }

    fn alldebrid_progress(info: &Value) -> i64 {
        let size = number(&info["size"]);
        if size != 0.0 {
            ((number(&info["downloaded"]) / size.max(1.0)) * 100.0) as i64
        } else {
            0
        }
    }

    /// AllDebrid: upload, then up to six checks a second apart.
    async fn alldebrid(
        &self,
        magnet: &str,
        season: Option<i64>,
        episode: Option<i64>,
    ) -> NetResult<Value> {
        let q = self.query(&[("agent", "KINO"), ("apikey", &self.token)]);
        let up = self
            .post(
                &format!("{}/magnet/upload?{q}", self.hosts.alldebrid),
                &[("magnets[]", magnet)],
                false,
            )
            .await?;
        let Some(first) = up["data"]["magnets"]
            .as_array()
            .and_then(|m| m.first().cloned())
        else {
            return Err(error("AllDebrid n'a pas pu ajouter ce magnet."));
        };
        let id = first["id"].clone();
        let id_text = match &id {
            Value::String(s) => s.clone(),
            other => other.to_string(),
        };
        let mut info = json!({});
        for _ in 0..6 {
            let status = self
                .get(
                    &format!("{}/magnet/status?{q}&id={id_text}", self.hosts.alldebrid),
                    false,
                )
                .await?;
            info = Self::alldebrid_info(&status);
            let code = number(&info["statusCode"]) as i64;
            if code == 4 {
                return self.alldebrid_unlock(&info, season, episode).await;
            }
            if code > 4 {
                return Err(error(format!(
                    "AllDebrid erreur magnet : {}",
                    info["status"].as_str().unwrap_or("")
                )));
            }
            tokio::time::sleep(Duration::from_secs(1)).await;
        }
        let status = info
            .get("status")
            .cloned()
            .unwrap_or(json!("Téléchargement Cloud AllDebrid"));
        Ok(not_ready(
            json!(format!("ad:{id_text}")),
            status,
            json!(Self::alldebrid_progress(&info)),
            number(&info["downloadSpeed"]),
            info.get("seeders").cloned().unwrap_or(json!(0)),
        ))
    }

    /// Debrid-Link: the seedbox lists the files already downloaded.
    async fn debridlink(
        &self,
        magnet: &str,
        season: Option<i64>,
        episode: Option<i64>,
    ) -> NetResult<Value> {
        let res = self
            .post(
                &format!("{}/seedbox/add", self.hosts.debridlink),
                &[("url", magnet), ("async", "true")],
                true,
            )
            .await?;
        let value = &res["value"];
        let files: Vec<Value> = value["files"]
            .as_array()
            .into_iter()
            .flatten()
            .filter(|f| {
                f["downloadUrl"].as_str().is_some_and(|u| !u.is_empty())
                    && number(&f["downloadPercent"]) == 100.0
            })
            .map(|f| {
                file_entry(
                    f["name"]
                        .as_str()
                        .filter(|n| !n.is_empty())
                        .unwrap_or("video.mkv"),
                    number(&f["size"]),
                    &f["downloadUrl"],
                    season,
                    episode,
                )
            })
            .collect();
        let id = json!(format!(
            "dl:{}",
            match &value["id"] {
                Value::String(s) => s.clone(),
                Value::Null => "None".into(),
                o => o.to_string(),
            }
        ));
        if !files.is_empty() {
            return Ok(ready(id, files));
        }
        Ok(not_ready(
            id,
            json!("Mise en cache Debrid-Link"),
            value.get("downloadPercent").cloned().unwrap_or(json!(0)),
            number(&value["downloadSpeed"]),
            value.get("peersConnected").cloned().unwrap_or(json!(0)),
        ))
    }

    /// Premiumize: only what it already has in its cache.
    async fn premiumize(
        &self,
        magnet: &str,
        season: Option<i64>,
        episode: Option<i64>,
    ) -> NetResult<Value> {
        let q = self.query(&[("apikey", &self.token)]);
        let res = self
            .post(
                &format!("{}/transfer/directdl?{q}", self.hosts.premiumize),
                &[("src", magnet)],
                false,
            )
            .await?;
        let mut files = Vec::new();
        for c in res["content"].as_array().into_iter().flatten() {
            let link = [&c["stream_link"], &c["link"]]
                .into_iter()
                .find(|v| v.as_str().is_some_and(|s| !s.is_empty()))
                .cloned();
            let name = c["path"]
                .as_str()
                .filter(|p| !p.is_empty())
                .unwrap_or("video.mkv")
                .rsplit('/')
                .next()
                .unwrap_or("video.mkv")
                .to_string();
            if let Some(link) = link
                && is_video(&name)
            {
                files.push(file_entry(
                    &name,
                    number(&c["size"]),
                    &link,
                    season,
                    episode,
                ));
            }
        }
        if files.is_empty() {
            return Err(error(
                "Ce magnet n'est pas encore en cache instantané sur Premiumize.",
            ));
        }
        Ok(ready(json!("pm_direct"), files))
    }

    /// TorBox: add the torrent, then read it like a known one.
    async fn torbox(
        &self,
        magnet: &str,
        season: Option<i64>,
        episode: Option<i64>,
    ) -> NetResult<Value> {
        let added = self
            .post(
                &format!("{}/torrents/createtorrent", self.hosts.torbox),
                &[("magnet", magnet)],
                true,
            )
            .await?;
        let id = &added["data"]["torrent_id"];
        if id.is_null() || id == &json!("") || id == &json!(0) {
            return Err(error(
                added["detail"]
                    .as_str()
                    .filter(|d| !d.is_empty())
                    .unwrap_or("TorBox n'a pas pu ajouter ce magnet."),
            ));
        }
        let id = match id {
            Value::String(s) => s.clone(),
            o => o.to_string(),
        };
        self.check(&format!("tb:{id}"), season, episode).await
    }

    /// Mega-Debrid: hoster links only (« login:password » logs in first).
    async fn megadebrid(&self, magnet: &str) -> NetResult<Value> {
        let mut token = self.token.clone();
        if let Some((login, password)) = self.token.split_once(':') {
            let res = self
                .get(
                    &format!(
                        "{}?{}",
                        self.hosts.megadebrid,
                        self.query(&[
                            ("form", "connexion"),
                            ("login", login),
                            ("password", password)
                        ])
                    ),
                    false,
                )
                .await?;
            token = res["token"].as_str().unwrap_or("").to_string();
        }
        if !magnet.starts_with("magnet:") {
            let res = self
                .post(
                    &format!(
                        "{}?{}",
                        self.hosts.megadebrid,
                        self.query(&[("form", "debrid"), ("token", &token)])
                    ),
                    &[("link", magnet)],
                    false,
                )
                .await?;
            if let Some(link) = res["debridLink"].as_str().filter(|l| !l.is_empty()) {
                let name = res["filename"]
                    .as_str()
                    .filter(|n| !n.is_empty())
                    .map(str::to_string)
                    .unwrap_or_else(|| {
                        let last = link
                            .rsplit('/')
                            .next()
                            .unwrap_or("")
                            .split('?')
                            .next()
                            .unwrap_or("");
                        form_urlencoded::parse(format!("x={last}").as_bytes())
                            .next()
                            .map(|(_, v)| v.into_owned())
                            .unwrap_or_default()
                    });
                return Ok(ready(
                    json!("md_direct"),
                    vec![
                        json!({"filename": name, "filesize": "Prêt (Mega-Debrid)", "filesize_bytes": 0, "download": link, "is_target_ep": true}),
                    ],
                ));
            }
        }
        Err(error(
            "Mega-Debrid débride les liens hébergeurs directs. Pour le streaming torrent P2P instantané, utilisez Real-Debrid, AllDebrid, TorBox, Debrid-Link ou Premiumize.",
        ))
    }

    /// Real-Debrid: add the magnet, choose its videos, wait a little, then unrestrict.
    async fn realdebrid(
        &self,
        magnet: &str,
        season: Option<i64>,
        episode: Option<i64>,
    ) -> NetResult<Value> {
        let added = self
            .post(
                &format!("{}/torrents/addMagnet", self.hosts.realdebrid),
                &[("magnet", magnet)],
                true,
            )
            .await?;
        let id = match &added["id"] {
            Value::String(s) => s.clone(),
            Value::Null => return Err(error("'id'")),
            o => o.to_string(),
        };
        let info = self
            .get(
                &format!("{}/torrents/info/{id}", self.hosts.realdebrid),
                true,
            )
            .await?;
        let files = info["files"].as_array().cloned().unwrap_or_default();
        let videos: Vec<String> = files
            .iter()
            .filter(|f| is_video(f["path"].as_str().unwrap_or("")) && number(&f["bytes"]) > MB25)
            .map(|f| match &f["id"] {
                Value::String(s) => s.clone(),
                o => o.to_string(),
            })
            .collect();
        let selected = if !videos.is_empty() {
            videos.join(",")
        } else if let Some(largest) = files
            .iter()
            .max_by(|a, b| number(&a["bytes"]).total_cmp(&number(&b["bytes"])))
        {
            match &largest["id"] {
                Value::String(s) => s.clone(),
                o => o.to_string(),
            }
        } else {
            "all".into()
        };
        if !files.is_empty() {
            self.post(
                &format!("{}/torrents/selectFiles/{id}", self.hosts.realdebrid),
                &[("files", &selected)],
                true,
            )
            .await?;
        }
        let mut info = json!({});
        for _ in 0..8 {
            info = self
                .get(
                    &format!("{}/torrents/info/{id}", self.hosts.realdebrid),
                    true,
                )
                .await?;
            match info["status"].as_str().unwrap_or("") {
                "downloaded" => break,
                s @ ("error" | "magnet_error" | "virus" | "dead") => {
                    return Err(error(format!(
                        "Real-Debrid a refusé ce torrent (statut : {s})"
                    )));
                }
                _ => tokio::time::sleep(Duration::from_secs(1)).await,
            }
        }
        if info["status"] != "downloaded" {
            return Ok(not_ready(
                json!(id),
                info["status"].clone(),
                info.get("progress").cloned().unwrap_or(json!(0)),
                number(&info["speed"]),
                info.get("seeders").cloned().unwrap_or(json!(0)),
            ));
        }
        self.unrestrict(&info, season, episode).await
    }

    async fn unrestrict(
        &self,
        info: &Value,
        season: Option<i64>,
        episode: Option<i64>,
    ) -> NetResult<Value> {
        let mut files = Vec::new();
        for link in info["links"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
        {
            if let Ok(u) = self
                .post(
                    &format!("{}/unrestrict/link", self.hosts.realdebrid),
                    &[("link", link)],
                    true,
                )
                .await
            {
                let name = u["filename"].as_str().unwrap_or("video.mkv").to_string();
                let size = [&u["filesize"], &u["size"]]
                    .into_iter()
                    .map(number)
                    .find(|n| *n != 0.0)
                    .unwrap_or(0.0);
                files.push(json!({"filename": name, "filesize": format_size(size), "filesize_bytes": size as i64,
                                  "download": u.get("download").cloned().unwrap_or(Value::Null), "is_target_ep": target(&name, season, episode)}));
            }
        }
        Ok(ready(info["id"].clone(), files))
    }

    /// Where a torrent the service is downloading stands, and its files once ready.
    pub async fn check(
        &self,
        torrent_id: &str,
        season: Option<i64>,
        episode: Option<i64>,
    ) -> NetResult<Value> {
        if torrent_id.starts_with("ad:") || self.provider == "alldebrid" {
            let id = torrent_id.rsplit(':').next().unwrap_or(torrent_id);
            let status = self
                .get(
                    &format!(
                        "{}/magnet/status?{}",
                        self.hosts.alldebrid,
                        self.query(&[("agent", "KINO"), ("apikey", &self.token), ("id", id)])
                    ),
                    false,
                )
                .await?;
            let info = Self::alldebrid_info(&status);
            if number(&info["statusCode"]) as i64 == 4 {
                return self.alldebrid_unlock(&info, season, episode).await;
            }
            return Ok(not_ready(
                json!(format!("ad:{id}")),
                info.get("status")
                    .cloned()
                    .unwrap_or(json!("Téléchargement AllDebrid")),
                json!(Self::alldebrid_progress(&info)),
                number(&info["downloadSpeed"]),
                info.get("seeders").cloned().unwrap_or(json!(0)),
            ));
        }
        if torrent_id.starts_with("tb:") || self.provider == "torbox" {
            let id = torrent_id.rsplit(':').next().unwrap_or(torrent_id);
            let res = self
                .get(
                    &format!(
                        "{}/torrents/mylist?bypass_cache=true&id={id}",
                        self.hosts.torbox
                    ),
                    true,
                )
                .await?;
            let info = match &res["data"] {
                Value::Array(l) => l.first().cloned().unwrap_or(json!({})),
                Value::Object(o) => Value::Object(o.clone()),
                _ => json!({}),
            };
            if info["download_present"].as_bool() == Some(true)
                && info["files"].as_array().is_some_and(|f| !f.is_empty())
            {
                let mut files = Vec::new();
                for f in info["files"].as_array().into_iter().flatten() {
                    let name = [&f["short_name"], &f["name"]]
                        .into_iter()
                        .find_map(|v| v.as_str().filter(|s| !s.is_empty()))
                        .unwrap_or("video.mkv")
                        .to_string();
                    if !is_video(&name) {
                        continue;
                    }
                    let fid = match &f["id"] {
                        Value::Null => "0".into(),
                        Value::String(s) => s.clone(),
                        o => o.to_string(),
                    };
                    let dl = self
                        .get(
                            &format!(
                                "{}/torrents/requestdl?token={}&torrent_id={id}&file_id={fid}",
                                self.hosts.torbox, self.token
                            ),
                            true,
                        )
                        .await?;
                    if let Some(url) = dl["data"].as_str().filter(|u| !u.is_empty()) {
                        files.push(file_entry(
                            &name,
                            number(&f["size"]),
                            &json!(url),
                            season,
                            episode,
                        ));
                    }
                }
                return Ok(ready(json!(format!("tb:{id}")), files));
            }
            return Ok(not_ready(
                json!(format!("tb:{id}")),
                info.get("download_state")
                    .cloned()
                    .unwrap_or(json!("Téléchargement TorBox")),
                json!((number(&info["progress"]) * 100.0) as i64),
                number(&info["download_speed"]),
                info.get("seeds").cloned().unwrap_or(json!(0)),
            ));
        }
        let info = self
            .get(
                &format!("{}/torrents/info/{torrent_id}", self.hosts.realdebrid),
                true,
            )
            .await?;
        if info["status"] != "downloaded" {
            return Ok(not_ready(
                json!(torrent_id),
                info["status"].clone(),
                info.get("progress").cloned().unwrap_or(json!(0)),
                number(&info["speed"]),
                info.get("seeders").cloned().unwrap_or(json!(0)),
            ));
        }
        self.unrestrict(&info, season, episode).await
    }
}
