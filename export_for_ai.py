#!/usr/bin/env python3
"""
Script d'exportation KINO pour ChatGPT / OpenAI Codex / Claude / LLMs.
Genere :
1. KINO_export_ai.zip : Archive zip propre du code source (sans build, dist, .git, cache).
2. KINO_codebase_context.md : Fichier Markdown unique consolidant l'architecture et les sources.
"""

import os
import zipfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent

EXCLUDE_DIRS = {
    ".git",
    "__pycache__",
    "dist",
    "build",
    ".venv",
    ".venv_win",
    "venv",
    ".pytest_cache",
    ".idea",
    ".vscode",
    ".agents",
    "bin",
    "KINO.app",
}

EXCLUDE_EXTENSIONS = {
    ".pyc",
    ".pyo",
    ".pyd",
    ".exe",
    ".zip",
    ".tar",
    ".gz",
    ".dmg",
    ".mp4",
    ".mkv",
    ".avi",
    ".mp3",
    ".db",
    ".sqlite",
    ".DS_Store",
    ".jsonl",
}

IMPORTANT_FILES = [
    "app.py",
    "desktop.py",
    "config.py",
    "kino_db.py",
    "player_engine.py",
    "debrid_engine.py",
    "meta_engine.py",
    "stream_engine.py",
    "torrent_engine.py",
    "anime_engine.py",
    "intro_engine.py",
    "community_lists.py",
    "addon_manager.py",
    "remote_controller.py",
    "trakt_engine.py",
    "discord_rpc.py",
    "sync_drive.py",
    "cli.py",
    "requirements.txt",
    "kino.spec",
    "GEMINI.md",
    "Lancer_KINO.bat",
    "Lancer_KINO.command",
    "Lancer_KINO_Silencieux.vbs",
    "web/index.html",
    "web/css/style.css",
    "web/js/app.js",
]


def should_include_file(path: Path) -> bool:
    rel_parts = path.relative_to(ROOT_DIR).parts
    for part in rel_parts[:-1]:
        if part in EXCLUDE_DIRS:
            return False
    if path.suffix in EXCLUDE_EXTENSIONS:
        return False
    if path.name.startswith("."):
        return False
    return True


def create_zip_export(output_zip: Path):
    print(f"Creation de l'archive ZIP : {output_zip.name}...")
    file_count = 0
    with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(ROOT_DIR):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            for file in files:
                filepath = Path(root) / file
                if should_include_file(filepath) and filepath != output_zip:
                    arcname = filepath.relative_to(ROOT_DIR)
                    zipf.write(filepath, arcname)
                    file_count += 1
    size_mb = output_zip.stat().st_size / (1024 * 1024)
    print(f"Archive ZIP creee : {file_count} fichiers inclus ({size_mb:.2f} Mo).")


def create_markdown_context(output_md: Path):
    print(f"Creation du fichier de contexte Markdown : {output_md.name}...")
    lines = [
        "# KINO — Codebase & Project Context",
        "",
        "Ce document consolide l'architecture, la structure et l'ensemble des fichiers sources du projet KINO.",
        "KINO est un Media Center autonome haute performance (Real-Debrid, TMDB, Stremio Addons, MPV / Lecteur interne HTML5).",
        "",
        "## Arborescence Principale",
        "```text",
    ]

    collected_files = []
    for root, dirs, files in os.walk(ROOT_DIR):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for file in files:
            filepath = Path(root) / file
            if should_include_file(filepath):
                rel = filepath.relative_to(ROOT_DIR)
                if rel.suffix in {".py", ".js", ".html", ".css", ".md", ".json", ".spec", ".bat", ".command", ".vbs"}:
                    collected_files.append(rel)

    collected_files.sort(key=lambda p: (len(p.parts), str(p)))
    for f in collected_files:
        lines.append(f"  {f.as_posix()}")
    lines.append("```")
    lines.append("")

    lines.append("## Fichiers Sources")
    lines.append("")

    for rel in collected_files:
        fullpath = ROOT_DIR / rel
        try:
            content = fullpath.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        ext = rel.suffix.lstrip(".")
        lang = {
            "py": "python",
            "js": "javascript",
            "html": "html",
            "css": "css",
            "json": "json",
            "md": "markdown",
            "spec": "python",
            "bat": "bat",
            "command": "bash",
            "vbs": "vbscript",
        }.get(ext, "")

        lines.append(f"### `{rel.as_posix()}`")
        lines.append(f"```{lang}")
        lines.append(content.rstrip())
        lines.append("```")
        lines.append("")

    output_md.write_text("\n".join(lines), encoding="utf-8")
    size_mb = output_md.stat().st_size / (1024 * 1024)
    print(f"Fichier Markdown cree : {len(collected_files)} fichiers documentes ({size_mb:.2f} Mo).")


def main():
    zip_path = ROOT_DIR / "KINO_export_ai.zip"
    md_path = ROOT_DIR / "KINO_codebase_context.md"

    create_zip_export(zip_path)
    create_markdown_context(md_path)

    print("\nExportation terminee avec succes.")
    print(f"- ZIP : {zip_path}")
    print(f"- Markdown : {md_path}")


if __name__ == "__main__":
    main()
