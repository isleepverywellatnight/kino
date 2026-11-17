//! What a release's name says: resolution, source, HDR, codec, audio, languages,
//! and whether the debrid service already has it.

use std::sync::LazyLock;

use regex::Regex;
use serde_json::{Value, json};

pub const INSTANT_BADGES: [&str; 7] = ["RD+", "AD+", "TB+", "DL+", "PM+", "OC+", "ED+"];
pub const FRENCH_AUDIO_LANGS: [&str; 5] = ["VFF", "VF", "VFQ", "MULTI", "FR"];

fn re(pattern: &str) -> Regex {
    Regex::new(pattern).expect(pattern)
}

/// Releases recorded in a cinema, fakes and inlaid ads.
pub static TRASH: LazyLock<Regex> = LazyLock::new(|| {
    re(
        r"(?i)\b(CAM|HDCAM|CAMRIP|TS|HDTS|TELESYNC|TELECINE|SCR|SCREENER|DVDSCREENER|WP|WORKPRINT|1XBET|BETWINNER|LINEBET|MELBET|PARI|KORSUB|HC|SUBHC|HARDCODED|SAMPLE|PROMO|TRAILER)\b",
    )
});

const SUB_LANGUAGE: &str = r"(?:french|fran[cç]ais|fre|fra|fr|english|eng|en|russian|rus|spanish|spa|es|german|ger|deu|italian|ita|japanese|jpn|por|portuguese)";
const SUB_SEPARATOR: &str = r"[\s._,;:/+\-]+";
const SUB_WORD: &str =
    r"(?:subs?|subbed|subtitles?|subtitled|sous[\s._\-]?titres?|sous[\s._\-]?titr[ée]s?)";

static SUBTITLE_CUES: LazyLock<[fancy_regex::Regex; 4]> = LazyLock::new(|| {
    let f = |p: String| fancy_regex::Regex::new(&p).expect("subtitle pattern");
    [
        f(format!(
            r"\b(?:multi|multilang){SUB_SEPARATOR}{SUB_WORD}(?!\w)(?:{SUB_SEPARATOR}{SUB_LANGUAGE}\b)*"
        )),
        f(format!(r"\b{SUB_WORD}{SUB_SEPARATOR}(?:multi|multilang)\b")),
        f(format!(
            r"\b(?:{SUB_LANGUAGE}{SUB_SEPARATOR}){{1,6}}{SUB_WORD}(?!\w)"
        )),
        f(format!(
            r"\b{SUB_WORD}(?!\w)(?:{SUB_SEPARATOR}{SUB_LANGUAGE}\b){{1,6}}"
        )),
    ]
});

/// Leaves out explicit subtitle labels before guessing the audio language.
fn without_subtitle_cues(text: &str) -> String {
    let mut out = text.to_string();
    for pattern in SUBTITLE_CUES.iter() {
        out = pattern.replace_all(&out, " ").into_owned();
    }
    out
}

macro_rules! patterns {
    ($($name:ident = $p:expr;)*) => { $(static $name: LazyLock<Regex> = LazyLock::new(|| re($p));)* };
}

patterns! {
    R4K = r"\b(2160p|4k|uhd)\b";
    R1080 = r"\b(1080p|fhd|1080i)\b";
    R720 = r"\b(720p|hd)\b";
    RSD = r"\b(480p|576p|sd|dvdrip|dvd|xvid)\b";
    SREMUX = r"\b(remux|bdremux|uhd[\.\-\s]?remux)\b";
    SBLURAY = r"\b(bluray|bdrip|brrip|blu-ray)\b";
    SWEB = r"\b(web-dl|webrip|web|amzn|nf|atvp|dsnp)\b";
    SHDTV = r"\b(hdtv|pdtv|dsr)\b";
    DV = r"\b(dv|dovi|dolby[\s\.\-]*vision)\b";
    HDR10P = r"\b(hdr10\+|hdr10plus)\b";
    HDR = r"\b(hdr|hdr10|hlg)\b";
    AV1 = r"\b(av1)\b";
    HEVC = r"\b(hevc|x265|h\.?265|265)\b";
    ATMOS = r"\b(atmos|dolby[\.\-\s]?atmos)\b";
    TRUEHD = r"\b(truehd|true-hd)\b";
    DTSHD = r"\b(dts[\.\-\s]?hd|dts[\.\-\s]?x|dts[\.\-\s]?ma)\b";
    DTS = r"\b(dts)\b";
    DDP = r"\b(e[\.\-\s]?ac3|eac3|ddp|dd\+|dolby[\.\-\s]?digital[\.\-\s]?plus)\b";
    AC3 = r"\b(ac3|dd5\.1|5\.1)\b";
    STEREO = r"\b(aac|2\.0|stereo)\b";
    MULTI = r"\b(multi|multilang)\b";
    FRENCH = r"\b(?:true)?french\b";
    ENGLISH = r"\b(?:eng|english)\b";
    VFF = r"\b(vff|truefrench|french[\.\-\s]?true)\b";
    VFQ = r"\b(vfq|quebec|french[\.\-\s]?qc)\b";
    VF = r"\b(vf|vf2|vfi|vof|french|fra|fre)\b";
    FR = r"\b(fr)\b";
    VOSTFR = r"\b(vostfr|subfrench)\b";
    ENG = r"\b(eng|english)\b";
}

/// Python's `\b` in `hdr10\+\b` needs a word character after the « + »: kept as is.
pub fn details(title: &str, meta: &str) -> Value {
    let raw = format!("{title} {meta}");
    let t = raw.to_lowercase();
    let resolution = if R4K.is_match(&t) {
        "4K"
    } else if R1080.is_match(&t) {
        "1080p"
    } else if R720.is_match(&t) {
        "720p"
    } else if RSD.is_match(&t) {
        "SD"
    } else {
        "1080p"
    };
    let source = if SREMUX.is_match(&t) {
        "REMUX"
    } else if SBLURAY.is_match(&t) {
        "BluRay"
    } else if SWEB.is_match(&t) {
        "WEB-DL"
    } else if SHDTV.is_match(&t) {
        "HDTV"
    } else if TRASH.is_match(&raw) {
        "CAM"
    } else {
        "WEB-DL"
    };
    let has_dv = DV.is_match(&t);
    let has_hdr10plus = HDR10P.is_match(&t);
    let has_hdr = HDR.is_match(&t) || has_hdr10plus;
    let hdr_type = match (has_dv, has_hdr, has_hdr10plus) {
        (true, true, _) => "DV HDR",
        (true, false, _) => "DV",
        (false, _, true) => "HDR10+",
        (false, true, false) => "HDR",
        _ => "SDR",
    };
    let codec = if AV1.is_match(&t) {
        "AV1"
    } else if HEVC.is_match(&t) {
        "HEVC"
    } else {
        "x264"
    };
    let mut audio = Vec::new();
    if ATMOS.is_match(&t) {
        audio.push("Atmos");
    }
    if TRUEHD.is_match(&t) {
        audio.push("TrueHD");
    }
    if DTSHD.is_match(&t) {
        audio.push("DTS-HD");
    } else if DTS.is_match(&t) {
        audio.push("DTS");
    }
    if DDP.is_match(&t) {
        audio.push("DDP");
    } else if AC3.is_match(&t) {
        audio.push("5.1");
    } else if STEREO.is_match(&t) {
        audio.push("2.0");
    }
    let audio_text = without_subtitle_cues(&t);
    let mut langs: Vec<&str> = Vec::new();
    // Whole words only: « Revenge » or « Avengers » must not read as an English track.
    if MULTI.is_match(&audio_text)
        || (FRENCH.is_match(&audio_text) && ENGLISH.is_match(&audio_text))
    {
        langs.push("MULTI");
    }
    if VFF.is_match(&audio_text) {
        langs.push("VFF");
    } else if VFQ.is_match(&audio_text) {
        langs.push("VFQ");
    } else if VF.is_match(&audio_text) {
        langs.push("VF");
    } else if FR.is_match(&audio_text) && langs.is_empty() {
        langs.push("FR");
    }
    if VOSTFR.is_match(&t) {
        langs.push("VOSTFR");
    } else if ENG.is_match(&audio_text)
        && !langs.iter().any(|l| ["VF", "VFF", "VFQ", "FR"].contains(l))
    {
        langs.push("VO");
    }
    let mut instant = INSTANT_BADGES.iter().any(|b| {
        t.contains(&format!("[{}]", b.to_lowercase()))
            || raw.contains(&format!("[{b}]"))
            || t.contains(&format!("({})", b.to_lowercase()))
    });
    if !instant && (raw.contains('⚡') || t.contains("cached")) {
        instant = true;
    }
    let mut badges = vec![resolution];
    if source == "REMUX" || source == "BluRay" {
        badges.push(source);
    }
    if hdr_type != "SDR" {
        badges.push(hdr_type);
    }
    if codec == "AV1" {
        badges.push("AV1");
    } else if codec == "HEVC" && resolution != "4K" {
        badges.push("HEVC");
    }
    if audio.contains(&"Atmos") {
        badges.push("Dolby Atmos");
    } else if audio.contains(&"TrueHD") {
        badges.push("TrueHD");
    } else if audio.contains(&"DTS-HD") {
        badges.push("DTS-HD");
    }
    json!({"resolution": resolution, "source": source, "hdr_type": hdr_type, "codec": codec, "audio_formats": audio,
           "langs": langs, "is_instant": instant, "display_badges": badges})
}
