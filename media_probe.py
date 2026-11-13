"""Inspect media tracks without exposing the source URL to the interface."""

import json
import re
import subprocess


def _track(stream):
    tags = stream.get("tags") or {}
    disposition = stream.get("disposition") or {}
    return {
        "index": int(stream["index"]),
        "codec": str(stream.get("codec_name") or "").lower(),
        "language": str(tags.get("language") or "und").lower(),
        "title": str(tags.get("title") or "")[:120],
        "default": bool(disposition.get("default")),
        "original": bool(disposition.get("original")),
        "channels": stream.get("channels"),
        "width": stream.get("width"),
        "height": stream.get("height"),
        "pixel_format": str(stream.get("pix_fmt") or ""),
        "fps": _frame_rate(stream.get("avg_frame_rate") or stream.get("r_frame_rate")),
    }


def _frame_rate(value):
    """« 24000/1001 » → 23.976; nothing usable → None."""
    try:
        if isinstance(value, str) and "/" in value:
            numerator, denominator = value.split("/", 1)
            rate = float(numerator) / float(denominator)
        else:
            rate = float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return round(rate, 3) if 1 < rate < 200 else None


def parse_probe_json(output):
    data = json.loads(output)
    streams = data.get("streams") or []
    return {
        "container": str((data.get("format") or {}).get("format_name") or ""),
        "duration": (data.get("format") or {}).get("duration"),
        "video": [_track(stream) for stream in streams if stream.get("codec_type") == "video" and "index" in stream],
        "audio": [_track(stream) for stream in streams if stream.get("codec_type") == "audio" and "index" in stream],
    }


def parse_ffmpeg_output(output):
    """Fallback when only the bundled FFmpeg executable is available."""
    result = {"container": "", "duration": None, "video": [], "audio": []}
    container = re.search(r"Input #\d+,\s*([^,]+(?:,[^,]+)*),\s*from ", output)
    if container:
        result["container"] = container.group(1).strip()
    duration = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", output)
    if duration:
        result["duration"] = str(int(duration.group(1)) * 3600 + int(duration.group(2)) * 60 + float(duration.group(3)))
    pattern = re.compile(
        r"Stream #\d+:(\d+)(?:\[[^]]+\])?(?:\(([^)]+)\))?:\s*(Video|Audio):\s*([\w]+)([^\n]*)",
        re.IGNORECASE,
    )
    for match in pattern.finditer(output):
        tail = match.group(5)
        stream = {
            "index": int(match.group(1)), "codec": match.group(4).lower(),
            "language": (match.group(2) or "und").lower(), "title": "",
            "default": "(default)" in tail, "original": "(original)" in tail,
            "channels": None, "width": None, "height": None, "pixel_format": "", "fps": None,
        }
        if match.group(3).lower() == "video":
            dimensions = re.search(r"\b(\d{2,5})x(\d{2,5})\b", tail)
            if dimensions:
                stream["width"], stream["height"] = map(int, dimensions.groups())
            pix_fmt = re.search(r"\b(yuv\w+|p010\w*|nv12)\b", tail)
            stream["pixel_format"] = pix_fmt.group(1) if pix_fmt else ""
            rate = re.search(r"\b(\d+(?:\.\d+)?) fps\b", tail)
            stream["fps"] = _frame_rate(rate.group(1)) if rate else None
            result["video"].append(stream)
        else:
            result["audio"].append(stream)
    return result


def inspect_media(source, ffmpeg, ffprobe=None, timeout=12):
    """Inspect a local file or remote stream, with bounded runtime and no shell."""
    if ffprobe:
        command = [ffprobe, "-v", "error", "-show_entries",
                   "format=format_name,duration:stream=index,codec_type,codec_name,channels,width,height,pix_fmt,avg_frame_rate:stream_tags=language,title:stream_disposition=default,original",
                   "-of", "json", source]
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
            if completed.returncode == 0:
                return parse_probe_json(completed.stdout)
        except (OSError, subprocess.TimeoutExpired, ValueError, json.JSONDecodeError):
            pass
    if not ffmpeg:
        raise RuntimeError("Media inspection is unavailable")
    try:
        completed = subprocess.run([ffmpeg, "-hide_banner", "-i", source], capture_output=True,
                                   text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError("Media inspection failed") from error
    parsed = parse_ffmpeg_output(completed.stderr)
    if not parsed["video"] and not parsed["audio"]:
        raise RuntimeError("No media tracks detected")
    return parsed


def select_audio_track(tracks, preference="vf"):
    """Choose a declared language, then a default track, then the first track."""
    if not tracks:
        return None
    wants_original = preference in ("vost", "vostfr", "vo")
    if wants_original:
        original = next((track for track in tracks if track.get("original")), None)
        if original:
            return original
    preferred = ("jpn", "ja", "japanese", "eng", "en", "english") if wants_original else ("fra", "fre", "fr", "french")
    for language in preferred:
        selected = next((track for track in tracks if track.get("language") == language), None)
        if selected:
            return selected
    return next((track for track in tracks if track.get("default")), tracks[0])
