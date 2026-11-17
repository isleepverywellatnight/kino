//! Who may talk to KINO's local server: the KINO window on this computer, and
//! from the local network only the phone remote with its pairing token.

use std::net::IpAddr;

use axum::http::HeaderMap;

const LOCAL_NAMES: [&str; 3] = ["127.0.0.1", "localhost", "::1"];
const REMOTE_PATHS: [&str; 3] = ["/remote", "/api/remote/state", "/api/remote/control"];

fn host_name(value: &str) -> &str {
    let value = value.trim();
    if let Some(rest) = value.strip_prefix('[') {
        return rest.split(']').next().unwrap_or("");
    }
    value.rsplit_once(':').map_or(value, |(h, port)| {
        if port.chars().all(|c| c.is_ascii_digit()) {
            h
        } else {
            value
        }
    })
}

/// The origin's host and port, for `http://host:port`.
fn origin_parts(origin: &str) -> Option<(String, Option<u16>)> {
    let rest = origin
        .strip_prefix("http://")
        .or_else(|| origin.strip_prefix("https://"))?;
    let authority = rest.split('/').next().unwrap_or("");
    let host = host_name(authority).to_string();
    let port = authority.rsplit_once(':').and_then(|(_, p)| p.parse().ok());
    Some((host, port))
}

/// Constant-time comparison for the pairing token.
pub fn same_secret(a: &str, b: &str) -> bool {
    a.len() == b.len()
        && a.bytes()
            .zip(b.bytes())
            .fold(0u8, |acc, (x, y)| acc | (x ^ y))
            == 0
}

pub fn request_allowed(
    peer: IpAddr,
    path: &str,
    headers: &HeaderMap,
    token: Option<&str>,
    server_port: u16,
    pairing_token: &str,
) -> bool {
    let header = |name: &str| {
        headers
            .get(name)
            .and_then(|v| v.to_str().ok())
            .unwrap_or("")
    };
    // A page of another site (an <img> or <video> pointing here) is labelled by the browser.
    if matches!(
        header("sec-fetch-site")
            .trim()
            .to_ascii_lowercase()
            .as_str(),
        "cross-site" | "same-site"
    ) {
        return false;
    }
    // The TV being cast to reads its stream from the network; the Python server
    // checks the session token and the TV's address (x-kino-peer).
    if path.starts_with("/tv/stream/") {
        return true;
    }
    if peer.is_loopback() {
        if !LOCAL_NAMES.contains(&host_name(header("host"))) {
            return false;
        }
        let origin = header("origin");
        if !origin.is_empty() {
            return match origin_parts(origin) {
                Some((host, port)) => {
                    LOCAL_NAMES.contains(&host.as_str()) && port == Some(server_port)
                }
                None => false,
            };
        }
        return true;
    }
    REMOTE_PATHS.contains(&path) && same_secret(token.unwrap_or(""), pairing_token)
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::http::HeaderValue;

    fn headers(pairs: &[(&'static str, &str)]) -> HeaderMap {
        let mut h = HeaderMap::new();
        for (k, v) in pairs {
            h.insert(*k, HeaderValue::from_str(v).unwrap());
        }
        h
    }

    const LOCAL: IpAddr = IpAddr::V4(std::net::Ipv4Addr::LOCALHOST);

    #[test]
    fn the_window_on_this_computer() {
        assert!(request_allowed(
            LOCAL,
            "/api/x",
            &headers(&[("host", "127.0.0.1:8080")]),
            None,
            8080,
            "t"
        ));
        assert!(request_allowed(
            LOCAL,
            "/",
            &headers(&[
                ("host", "localhost:8080"),
                ("origin", "http://127.0.0.1:8080")
            ]),
            None,
            8080,
            "t"
        ));
        assert!(
            !request_allowed(
                LOCAL,
                "/",
                &headers(&[("host", "evil.example:8080")]),
                None,
                8080,
                "t"
            ),
            "DNS rebinding"
        );
        assert!(!request_allowed(
            LOCAL,
            "/",
            &headers(&[
                ("host", "127.0.0.1:8080"),
                ("origin", "http://127.0.0.1:9999")
            ]),
            None,
            8080,
            "t"
        ));
        assert!(!request_allowed(
            LOCAL,
            "/",
            &headers(&[("host", "127.0.0.1:8080"), ("sec-fetch-site", "cross-site")]),
            None,
            8080,
            "t"
        ));
    }

    #[test]
    fn the_tv_reads_its_stream_and_the_python_server_checks_it() {
        let tv: IpAddr = "192.168.1.30".parse().unwrap();
        let h = headers(&[("host", "192.168.1.10:8080")]);
        assert!(request_allowed(
            tv,
            "/tv/stream/token/0",
            &h,
            None,
            8080,
            "secret"
        ));
        assert!(!request_allowed(tv, "/tv/other", &h, None, 8080, "secret"));
    }

    #[test]
    fn the_network_only_reaches_the_remote_with_its_token() {
        let phone: IpAddr = "192.168.1.20".parse().unwrap();
        let h = headers(&[("host", "192.168.1.10:8080")]);
        assert!(request_allowed(
            phone,
            "/remote",
            &h,
            Some("secret"),
            8080,
            "secret"
        ));
        assert!(!request_allowed(
            phone,
            "/remote",
            &h,
            Some("wrong!"),
            8080,
            "secret"
        ));
        assert!(!request_allowed(
            phone,
            "/api/config",
            &h,
            Some("secret"),
            8080,
            "secret"
        ));
    }
}
