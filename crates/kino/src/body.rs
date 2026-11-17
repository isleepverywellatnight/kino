//! The body of a POST request, read as the Python server reads it: empty is {},
//! anything but a JSON object is refused with a 400 (no Content-Type needed).

use axum::Json;
use axum::body::Bytes;
use axum::extract::{FromRequest, Request};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use serde_json::{Value, json};

pub struct JsonBody(pub Value);

impl<S: Send + Sync> FromRequest<S> for JsonBody {
    type Rejection = Response;

    async fn from_request(request: Request, state: &S) -> Result<Self, Self::Rejection> {
        let refused = || {
            (
                StatusCode::BAD_REQUEST,
                Json(json!({"error": "Requête invalide : un objet JSON est attendu."})),
            )
                .into_response()
        };
        let bytes = Bytes::from_request(request, state)
            .await
            .map_err(|_| refused())?;
        if bytes.is_empty() {
            return Ok(JsonBody(json!({})));
        }
        match serde_json::from_slice::<Value>(&bytes) {
            Ok(value) if value.is_object() => Ok(JsonBody(value)),
            _ => Err(refused()),
        }
    }
}
