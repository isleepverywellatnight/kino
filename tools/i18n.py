#!/usr/bin/env python3
"""KINO's interface translations.

The interface is written in French. web/js/i18n.js translates it while it is
displayed, with one dictionary per language: web/js/i18n/<lang>.js, which maps
each French text to its translation. "{}" stands for a variable part:
"{} épisodes" -> "{} episodes" (use {1}, {2}… to reorder them).

  python tools/i18n.py extract   list the French texts of web/ in web/js/i18n/strings.json
  python tools/i18n.py status    count, per language, the texts still untranslated
  python tools/i18n.py missing de    print the texts German still lacks (JSON)

A missing text stays in French: translations can be completed little by little.
"""
import html
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
WEB = PROJECT / "web"
I18N = WEB / "js" / "i18n"
CATALOG = I18N / "strings.json"
PREFIX, SUFFIX = "KINO_I18N.load(", ");\n"
ATTRIBUTES = ("placeholder", "title", "aria-label", "alt", "data-tooltip", "data-label")
# Words that only appear in code: a literal made of them alone is not shown text.
CODE_HINTS = re.compile(r"=>|\bfunction\b|\breturn\b|;\s*$|^\s*[.#\[]|var\(--|rgba?\(|\bpx\b|https?://|\.(js|css|svg|png|json)\b"
                        r"|^[a-z]+[A-Z]\w*$|^[\w-]+=|^\{\}$|^[a-z0-9_-]+$")
# JavaScript caught between two literals: calls, operators, statements.
CODE_SYNTAX = re.compile(r"[A-Za-z_$]\(|\)\s*[;{]|\)\.[A-Za-z_$]|[;{}]\s*$|^\s*[;)\]}]|==|&&|\|\||\?\s*'|\b(const|let|var|if|else)\s+[\w(]"
                         r"|\.(style|push|join|map|get|set|forEach|classList)\b")
LETTER = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]")
FRENCH_HINT = re.compile(r"[À-ÖØ-öø-ÿ«»’]|^[A-ZÀ-Ö]|\s")


def normalize(text):
    return " ".join(text.split())


def is_shown_text(text):
    text = normalize(text)
    if not text or not LETTER.search(text) or len(text) > 400:
        return False
    bare = text.replace("{}", "")  # variables are not code
    if CODE_HINTS.search(text) or CODE_SYNTAX.search(bare) or not FRENCH_HINT.search(text):
        return False
    if re.fullmatch(r"[A-Z0-9_+\-]{1,3}|[A-Z][A-Z_]{3,}|[A-Z][a-z]+(?:[A-Z][a-z]+)+(Error)?|[A-Z]{4,}[0-9]+", text):
        return False  # key names, tags, error names: ArrowDown, INPUT, NotAllowedError…
    return not re.fullmatch(r"[\w.-]+(\s+[\w.-]+)*", text) or bool(re.search(r"[A-ZÀ-Öà-ÿ]", text))


def text_segments(literal):
    """The pieces of a JS literal that can end up as text: HTML markup is split
    away, ${…} becomes {}, and a literal that begins or ends with a space or a
    colon is completed by {} (it is concatenated with something)."""
    out = []
    # Attribute values written in HTML templates (title="…") are text too.
    attribute = re.compile(r"\b(?:%s)=(\"([^\"]*)\"|'([^']*)')" % "|".join(ATTRIBUTES))
    for match in attribute.finditer(literal):
        value = normalize(html.unescape(match.group(2) if match.group(2) is not None else match.group(3)))
        if value and value != "{}":
            out.append(value)
    for piece in re.split(r"<[^<>]*>", literal):
        if "<" in piece or ">" in piece or '="' in piece or "='" in piece:
            continue
        piece = html.unescape(piece)
        piece = piece.replace("\\n", " ").replace("\\'", "'").replace('\\"', '"')
        core = normalize(piece)
        if not core:
            continue
        if piece[:1].isspace() and not core.startswith("{}"):
            core = "{} " + core
        if (piece[-1:].isspace() or core.endswith(":")) and not core.endswith("{}"):
            core = core + " {}"
        out.append(core)
    return out


def js_literals(source):
    """String and template literals of a JavaScript file (templates with ${…} as {})."""
    i, n, out = 0, len(source), []
    prev = ""  # last significant character, to tell a regex from a division

    def template(start):
        j, buf = start, []
        while j < n:
            c = source[j]
            if c == "\\":
                buf.append(source[j:j + 2]); j += 2; continue
            if c == "`":
                return "".join(buf), j + 1
            if c == "$" and source[j + 1:j + 2] == "{":
                depth, j = 1, j + 2
                while j < n and depth:
                    if source[j] == "`":
                        inner, j = template(j + 1)
                        out.append(inner)
                        continue
                    if source[j] in "'\"":
                        q = source[j]; j += 1
                        while j < n and source[j] != q:
                            j += 2 if source[j] == "\\" else 1
                    depth += {"{": 1, "}": -1}.get(source[j], 0)
                    j += 1
                buf.append("{}")
                continue
            buf.append(c); j += 1
        return "".join(buf), j

    while i < n:
        c = source[i]
        if source.startswith("//", i):
            i = source.find("\n", i)
            i = n if i < 0 else i
            continue
        if source.startswith("/*", i):
            i = source.find("*/", i + 2)
            i = n if i < 0 else i + 2
            continue
        if c in "'\"":
            j = i + 1
            while j < n and source[j] != c and source[j] != "\n":
                j += 2 if source[j] == "\\" else 1
            out.append(source[i + 1:j]); i = j + 1; prev = "a"; continue
        if c == "`":
            text, i = template(i + 1)
            out.append(text); prev = "a"; continue
        if c == "/" and (prev in "(,=:[!&|?{};+-*%<>~^" or prev == ""):
            j, in_class = i + 1, False
            while j < n and source[j] != "\n":
                if source[j] == "\\":
                    j += 2; continue
                if source[j] == "[":
                    in_class = True
                elif source[j] == "]":
                    in_class = False
                elif source[j] == "/" and not in_class:
                    break
                j += 1
            i = j + 1; prev = "a"; continue
        if not c.isspace():
            word = re.match(r"[A-Za-z_$][\w$]*", source[i:])
            if word:
                prev = "(" if word.group() in ("return", "typeof", "case", "in", "of", "else", "do") else "a"
                i += len(word.group()); continue
            prev = c
        i += 1
    return out


class _Html(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.texts, self.scripts, self._in = [], [], None

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._in = tag
        for name, value in attrs:
            if name in ATTRIBUTES and value:
                self.texts.append(value)

    def handle_endtag(self, tag):
        if tag == self._in:
            self._in = None

    def handle_data(self, data):
        if self._in == "script":
            self.scripts.append(data)
        elif self._in is None:
            self.texts.append(data)


def extract():
    found = set()
    for page in sorted(WEB.glob("*.html")):
        parser = _Html()
        parser.feed(page.read_text(encoding="utf-8"))
        found.update(normalize(t) for t in parser.texts)
        for script in parser.scripts:
            for literal in js_literals(script):
                found.update(text_segments(literal))
    for script in sorted((WEB / "js").glob("*.js")):
        for literal in js_literals(script.read_text(encoding="utf-8")):
            found.update(text_segments(literal))
    return sorted(t for t in found if is_shown_text(t))


def read_language(lang):
    path = I18N / f"{lang}.js"
    if not path.is_file():
        return {}
    raw = path.read_text(encoding="utf-8")
    return json.loads(raw[raw.index(PREFIX) + len(PREFIX):raw.rindex(")")])


def write_language(lang, table):
    body = json.dumps(dict(sorted(table.items())), ensure_ascii=False, indent=0)
    (I18N / f"{lang}.js").write_text(f"// KINO interface, French -> {lang}. See tools/i18n.py.\n{PREFIX}{body}{SUFFIX}",
                                     encoding="utf-8")


def languages():
    return sorted(p.stem for p in I18N.glob("*.js"))


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else "status"
    if command == "extract":
        strings = extract()
        CATALOG.write_text(json.dumps(strings, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
        print(f"{len(strings)} textes dans {CATALOG.relative_to(PROJECT)}")
    elif command == "status":
        strings = json.loads(CATALOG.read_text(encoding="utf-8"))
        for lang in languages():
            table = read_language(lang)
            missing = sum(1 for s in strings if s not in table)
            print(f"{lang}: {len(strings) - missing}/{len(strings)} traduits")
    elif command == "missing":
        strings = json.loads(CATALOG.read_text(encoding="utf-8"))
        table = read_language(sys.argv[2])
        print(json.dumps([s for s in strings if s not in table], ensure_ascii=False, indent=0))
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
