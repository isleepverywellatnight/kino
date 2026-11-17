//! One HTTP client for all of KINO (one TLS configuration, shared connections)
//! and the two-level cache: memory, then SQLite, with the same keys as the
//! Python version so both read one cache.

use std::collections::HashMap;
use std::future::Future;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use reqwest::header::{HeaderMap, HeaderName, HeaderValue, USER_AGENT};
use serde_json::Value;

use crate::db::{Db, now};

pub const BROWSER: &str = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 \
                           (KHTML, like Gecko) Version/18.0 Safari/605.1.15";
pub const KINO_AGENT: &str = "KINO-Desktop (https://github.com/isleepverywellatnight/kino)";

/// A site that did not answer, or answered with an error or something unreadable.
#[derive(Debug)]
pub struct NetError(pub String);

impl std::fmt::Display for NetError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for NetError {}

impl From<reqwest::Error> for NetError {
    fn from(error: reqwest::Error) -> Self {
        NetError(error.to_string())
    }
}

pub type NetResult<T> = Result<T, NetError>;

pub struct Net {
    pub client: reqwest::Client,
    db: Arc<Db>,
    memory: Mutex<HashMap<String, (f64, Value)>>,
}

fn headers(pairs: &[(&str, &str)], agent: &str) -> HeaderMap {
    let mut map = HeaderMap::new();
    map.insert(
        USER_AGENT,
        HeaderValue::from_str(agent).unwrap_or(HeaderValue::from_static("KINO")),
    );
    for (k, v) in pairs {
        if let (Ok(name), Ok(value)) = (HeaderName::try_from(*k), HeaderValue::from_str(v)) {
            map.insert(name, value);
        }
    }
    map
}

impl Net {
    pub fn new(db: Arc<Db>) -> Self {
        let client = reqwest::Client::builder()
            .connect_timeout(Duration::from_secs(8))
            .timeout(Duration::from_secs(20))
            .gzip(true)
            .build()
            .expect("HTTP client");
        Self {
            client,
            db,
            memory: Mutex::new(HashMap::new()),
        }
    }

    pub fn db(&self) -> &Db {
        &self.db
    }

    async fn check(response: reqwest::Response) -> NetResult<reqwest::Response> {
        let status = response.status();
        if status.is_success() {
            return Ok(response);
        }
        let host = response
            .url()
            .host_str()
            .unwrap_or("service distant")
            .to_string();
        Err(NetError(format!(
            "Erreur API ({}) de {host}",
            status.as_u16()
        )))
    }

    pub async fn get_text(
        &self,
        url: &str,
        extra: &[(&str, &str)],
        agent: &str,
        timeout: Duration,
    ) -> NetResult<String> {
        let response = self
            .client
            .get(url)
            .headers(headers(extra, agent))
            .timeout(timeout)
            .send()
            .await?;
        Ok(Self::check(response).await?.text().await?)
    }

    pub async fn get_json(&self, url: &str, agent: &str, timeout: Duration) -> NetResult<Value> {
        let response = self
            .client
            .get(url)
            .headers(headers(&[], agent))
            .timeout(timeout)
            .send()
            .await?;
        let body = Self::check(response).await?.text().await?;
        if body.trim().is_empty() {
            return Ok(Value::Object(Default::default()));
        }
        serde_json::from_str(&body).map_err(|_| NetError("Réponse JSON invalide".into()))
    }

    pub async fn post_json(
        &self,
        url: &str,
        body: &Value,
        agent: &str,
        timeout: Duration,
    ) -> NetResult<Value> {
        let response = self
            .client
            .post(url)
            .headers(headers(&[], agent))
            .json(body)
            .timeout(timeout)
            .send()
            .await?;
        let text = Self::check(response).await?.text().await?;
        serde_json::from_str(&text).map_err(|_| NetError("Réponse JSON invalide".into()))
    }

    /// A JSON request with headers, retried on 429 and 5xx answers as the Python
    /// version does (`retries` more times, waiting 1 s then 2 s).
    pub async fn request_json(
        &self,
        method: reqwest::Method,
        url: &str,
        extra: &[(&str, &str)],
        body: Option<&Value>,
        timeout: Duration,
        retries: u32,
    ) -> NetResult<Value> {
        let mut attempt = 0;
        loop {
            let mut request = self
                .client
                .request(method.clone(), url)
                .headers(headers(extra, "KINO-Desktop"))
                .timeout(timeout);
            if let Some(body) = body {
                request = request.json(body);
            }
            let outcome = request.send().await;
            let retry = match &outcome {
                Ok(r) => matches!(r.status().as_u16(), 429 | 500 | 502 | 503 | 504),
                Err(e) => !e.is_connect(),
            };
            if retry && attempt < retries {
                tokio::time::sleep(Duration::from_secs(1 << attempt.min(1))).await;
                attempt += 1;
                continue;
            }
            let response = outcome?;
            let status = response.status();
            let host = response
                .url()
                .host_str()
                .unwrap_or("service distant")
                .to_string();
            let text = response.text().await?;
            if !status.is_success() {
                let detail = serde_json::from_str::<Value>(&text)
                    .ok()
                    .and_then(|v| {
                        v.get("error").or(v.get("detail")).map(|e| {
                            e.as_str()
                                .map(str::to_string)
                                .unwrap_or_else(|| e.to_string())
                        })
                    })
                    .unwrap_or_else(|| text.clone());
                let detail = detail.split_whitespace().collect::<Vec<_>>().join(" ");
                let detail = if detail.is_empty() {
                    "réponse vide".to_string()
                } else if detail.starts_with('<') {
                    "page d'erreur du service".to_string()
                } else {
                    detail.chars().take(200).collect()
                };
                return Err(NetError(format!(
                    "Erreur API ({}) de {host} : {detail}",
                    status.as_u16()
                )));
            }
            if text.trim().is_empty() {
                return Ok(Value::Object(Default::default()));
            }
            return serde_json::from_str(&text)
                .map_err(|_| NetError(format!("Réponse JSON invalide de {host}")));
        }
    }

    /// A value from the memory cache, then SQLite, else fetched and kept for `ttl` seconds.
    /// A null answer is not kept.
    pub async fn cached<F, Fut>(&self, key: &str, ttl: f64, fetch: F) -> NetResult<Value>
    where
        F: FnOnce() -> Fut,
        Fut: Future<Output = NetResult<Value>>,
    {
        let at = now();
        if let Some((stored, value)) = self
            .memory
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .get(key)
            && at - stored < ttl
        {
            return Ok(value.clone());
        }
        if let Some(value) = self.db.cache_get(key) {
            self.remember(key, &value, at);
            return Ok(value);
        }
        let value = fetch().await?;
        if !value.is_null() {
            self.remember(key, &value, at);
            let _ = self.db.cache_set(key, &value, ttl);
        }
        Ok(value)
    }

    fn remember(&self, key: &str, value: &Value, at: f64) {
        let mut memory = self.memory.lock().unwrap_or_else(|e| e.into_inner());
        if memory.len() > 2000 {
            memory.clear();
        }
        memory.insert(key.to_string(), (at, value.clone()));
    }
}
