//! A .torrent file as a magnet link: its info hash (SHA-1 of the exact bytes
//! of its « info » section), its name and up to eight trackers.

use sha1::{Digest, Sha1};
use std::collections::BTreeMap;

enum Bencode {
    Int,
    Bytes(Vec<u8>),
    List(Vec<Bencode>),
    Dict(BTreeMap<String, Bencode>, Option<(usize, usize)>),
}

/// One value at `at`; a dictionary remembers where its « info » value lies.
fn decode(data: &[u8], at: usize, depth: usize) -> Option<(Bencode, usize)> {
    if depth > 64 {
        return None;
    }
    match *data.get(at)? {
        b'i' => {
            let end = at + 1 + data[at + 1..].iter().position(|b| *b == b'e')?;
            std::str::from_utf8(&data[at + 1..end])
                .ok()?
                .parse::<i64>()
                .ok()?;
            Some((Bencode::Int, end + 1))
        }
        b'l' => {
            let (mut items, mut i) = (vec![], at + 1);
            while *data.get(i)? != b'e' {
                let (item, next) = decode(data, i, depth + 1)?;
                items.push(item);
                i = next;
            }
            Some((Bencode::List(items), i + 1))
        }
        b'd' => {
            let (mut map, mut info, mut i) = (BTreeMap::new(), None, at + 1);
            while *data.get(i)? != b'e' {
                let (Bencode::Bytes(key), next) = decode(data, i, depth + 1)? else {
                    return None;
                };
                let (value, end) = decode(data, next, depth + 1)?;
                let key = String::from_utf8_lossy(&key).into_owned();
                if key == "info" && matches!(value, Bencode::Dict(..)) {
                    info = Some((next, end));
                }
                map.insert(key, value);
                i = end;
            }
            Some((Bencode::Dict(map, info), i + 1))
        }
        b'0'..=b'9' => {
            let colon = at + data[at..].iter().position(|b| *b == b':')?;
            let length: usize = std::str::from_utf8(&data[at..colon]).ok()?.parse().ok()?;
            let end = (colon + 1)
                .checked_add(length)
                .filter(|e| *e <= data.len())?;
            Some((Bencode::Bytes(data[colon + 1..end].to_vec()), end))
        }
        _ => None,
    }
}

/// (magnet, name, info hash), or the reason the file is refused.
pub fn to_magnet(raw: &[u8]) -> Result<(String, String, String), String> {
    let refused = || "Ce fichier .torrent ne contient pas de section 'info' valide.".to_string();
    let Some((Bencode::Dict(meta, Some((start, end))), _)) = decode(raw, 0, 0) else {
        return Err(refused());
    };
    let hash = hex::encode(Sha1::digest(&raw[start..end]));
    let Some(Bencode::Dict(info, _)) = meta.get("info") else {
        return Err(refused());
    };
    let name = match info.get("name.utf-8").or_else(|| info.get("name")) {
        Some(Bencode::Bytes(b)) if !b.is_empty() => String::from_utf8_lossy(b).into_owned(),
        _ => "Torrent".into(),
    };
    let mut trackers: Vec<String> = vec![];
    if let Some(Bencode::Bytes(a)) = meta.get("announce") {
        trackers.push(String::from_utf8_lossy(a).into_owned());
    }
    if let Some(Bencode::List(tiers)) = meta.get("announce-list") {
        for tier in tiers {
            if let Bencode::List(urls) = tier {
                for url in urls {
                    if let Bencode::Bytes(u) = url {
                        let u = String::from_utf8_lossy(u).into_owned();
                        if !u.is_empty() && !trackers.contains(&u) {
                            trackers.push(u);
                        }
                    }
                }
            }
        }
    }
    let quote = crate::sources::stream::py_quote;
    let tr: String = trackers
        .iter()
        .take(8)
        .map(|t| format!("&tr={}", quote(t)))
        .collect();
    let magnet = format!("magnet:?xt=urn:btih:{hash}&dn={}{tr}", quote(&name));
    Ok((magnet, name, hash))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn magnet_of_a_file() {
        let info = b"d6:lengthi1024e4:name10:Dune 2.mkv12:piece lengthi16384e6:pieces20:aaaaaaaaaaaaaaaaaaaae";
        let mut raw = b"d8:announce30:udp://tracker.example:1337/ann13:announce-listll30:udp://tracker.example:1337/annel21:https://t2.example/anee4:info".to_vec();
        raw.extend_from_slice(info);
        raw.push(b'e');
        let (magnet, name, hash) = to_magnet(&raw).unwrap();
        assert_eq!(hash, hex::encode(Sha1::digest(info)));
        assert_eq!(name, "Dune 2.mkv");
        assert_eq!(
            magnet,
            format!(
                "magnet:?xt=urn:btih:{hash}&dn=Dune%202.mkv&tr=udp%3A//tracker.example%3A1337/ann&tr=https%3A//t2.example/an"
            )
        );
        assert!(to_magnet(b"d4:spam4:eggse").is_err());
        assert!(to_magnet(b"garbage").is_err());
    }
}
