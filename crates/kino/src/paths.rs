//! Where KINO keeps its files. The same places as the Python version, so both
//! read and write one library while KINO moves to Rust.

use std::path::{Path, PathBuf};

/// The user's home folder: `$HOME` first (tests point it at a temporary folder).
pub fn home() -> PathBuf {
    std::env::var_os("HOME")
        .filter(|h| !h.is_empty())
        .map(PathBuf::from)
        .or_else(dirs::home_dir)
        .unwrap_or_else(|| PathBuf::from("."))
}

/// Library, history, ratings, settings stored as JSON, and the cache.
pub fn database() -> PathBuf {
    home().join(".kino_data.db")
}

/// Settings and service keys.
pub fn config_file() -> PathBuf {
    home().join(".rd_cinehub_config.json")
}

/// The interface (HTML, CSS, JavaScript): `KINO_WEB`, else next to the program
/// (in the app bundle), else the repository's `web/` folder.
pub fn web_root() -> PathBuf {
    if let Some(dir) = std::env::var_os("KINO_WEB") {
        return PathBuf::from(dir);
    }
    let mut candidates = Vec::new();
    if let Ok(exe) = std::env::current_exe()
        && let Some(dir) = exe.parent()
    {
        candidates.push(dir.join("web"));
        candidates.push(dir.join("../Resources/web"));
    }
    candidates.push(Path::new(env!("CARGO_MANIFEST_DIR")).join("../../web"));
    candidates
        .into_iter()
        .find(|c| c.join("index.html").is_file())
        .unwrap_or_else(|| PathBuf::from("web"))
}
