//! The source search against what the Python version computed on real indexer
//! answers (tools/make_source_fixtures.py writes the fixtures).

use std::io::Read;
use std::sync::LazyLock;

use serde_json::Value;

static FIXTURES: LazyLock<Value> = LazyLock::new(|| {
    let raw = std::fs::read(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/fixtures/sources.json.gz"
    ))
    .unwrap();
    let mut text = String::new();
    flate2::read::GzDecoder::new(&raw[..])
        .read_to_string(&mut text)
        .unwrap();
    serde_json::from_str(&text).unwrap()
});

#[test]
fn every_release_name_reads_as_in_python() {
    let details = FIXTURES["details"].as_object().unwrap();
    let mut wrong = Vec::new();
    for (title, expected) in details {
        let got = kino::sources::release::details(title, "");
        if &got != expected {
            wrong.push(format!("{title}\n  python: {expected}\n  rust:   {got}"));
        }
    }
    assert!(
        wrong.is_empty(),
        "{} of {} differ:\n{}",
        wrong.len(),
        details.len(),
        wrong.iter().take(8).cloned().collect::<Vec<_>>().join("\n")
    );
}

use kino::config::{HdrMode, PrefLang, Quality, SourcePriority};
use kino::sources::stream::{self, Wanted};
use serde_json::{Map, json};

fn buckets(case: &Value) -> Map<String, Value> {
    case["raw"]
        .as_object()
        .unwrap()
        .iter()
        .map(|(bucket, data)| {
            let label = data["label"].as_str().unwrap();
            let parsed: Vec<Value> = data["streams"]
                .as_array()
                .unwrap()
                .iter()
                .filter_map(|s| stream::parse_stremio_stream(s, label))
                .collect();
            (bucket.clone(), Value::Array(parsed))
        })
        .collect()
}

#[test]
fn indexer_answers_read_and_merge_as_in_python() {
    for case in FIXTURES["cases"].as_array().unwrap() {
        let merged = stream::merge(&buckets(case));
        let expected = case["merged"].as_array().unwrap();
        assert_eq!(merged.len(), expected.len(), "{}", case["title"]);
        for (got, want) in merged.iter().zip(expected) {
            assert_eq!(got, want, "{}", case["title"]);
        }
    }
}

#[test]
fn releases_kept_scored_and_sorted_as_in_python() {
    let mut checked = 0;
    for case in FIXTURES["cases"].as_array().unwrap() {
        let merged = stream::merge(&buckets(case));
        let titles = vec![case["title"].as_str().unwrap().to_string()];
        for result in case["results"].as_array().unwrap() {
            let p = &result["profile"];
            let wanted = Wanted {
                media_type: case["media_type"].as_str().unwrap(),
                season: case["season"].as_i64().unwrap_or(1),
                episode: case["episode"].as_i64().unwrap_or(1),
                absolute_episode: case["absolute"].as_i64(),
                runtime_minutes: case["runtime"].as_f64(),
                titles: &titles,
                release_year: case["year"].as_str().unwrap(),
                pref_lang: PrefLang::parse(p["pref_lang"].as_str().unwrap()).unwrap(),
                pref_quality: Quality::parse(p["pref_quality"].as_str().unwrap()).unwrap(),
            };
            let kept = stream::keep_relevant(merged.clone(), &wanted);
            let sort_by = result["sort_by"].as_str().unwrap();
            let ranked = stream::rank(
                kept,
                sort_by,
                &wanted,
                HdrMode::parse(p["hdr_mode"].as_str().unwrap()).unwrap(),
                SourcePriority::parse(p["source_priority"].as_str().unwrap()).unwrap(),
            );
            let key = |item: &Value| {
                let hash = item["info_hash"].as_str().unwrap_or("");
                if hash.is_empty() {
                    item["title"].clone()
                } else {
                    json!(hash)
                }
            };
            let expected = result["ranked"].as_array().unwrap();
            let context = format!("{} {p} {sort_by}", case["title"]);
            if sort_by == "score" {
                let got: Vec<Value> = ranked
                    .iter()
                    .map(|i| json!({"key": key(i), "score": i["score"], "parts": i["score_parts"], "why": i["why"], "recommendation": i["recommendation"]}))
                    .collect();
                for (g, w) in got.iter().zip(expected) {
                    assert_eq!(g, w, "{context}");
                }
                assert_eq!(got.len(), expected.len(), "{context}");
            } else {
                let got: Vec<Value> = ranked.iter().map(key).collect();
                assert_eq!(&got, expected, "{context}");
            }
            checked += 1;
        }
    }
    assert_eq!(checked, 160);
}

#[test]
fn episode_files_are_recognised_as_in_python() {
    let raw = std::fs::read(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/fixtures/episodes.json.gz"
    ))
    .unwrap();
    let mut text = String::new();
    flate2::read::GzDecoder::new(&raw[..])
        .read_to_string(&mut text)
        .unwrap();
    let rows: Vec<Value> = serde_json::from_str(&text).unwrap();
    let mut wrong = Vec::new();
    for row in &rows {
        let got = kino::resolve::is_target_episode(
            row[0].as_str().unwrap(),
            row[1].as_i64(),
            row[2].as_i64(),
            row[3].as_i64(),
        );
        if got != row[4].as_bool().unwrap() {
            wrong.push(row.to_string());
        }
    }
    assert!(
        wrong.is_empty(),
        "{} of {} differ: {:?}",
        wrong.len(),
        rows.len(),
        &wrong[..wrong.len().min(10)]
    );
}
