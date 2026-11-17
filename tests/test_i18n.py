"""Interface dictionaries: valid files, and variables that the engine can fill."""
import json
import re
import sys
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT / "tools"))

import i18n  # noqa: E402

LANGUAGES = i18n.languages()
ENGINE = (PROJECT / "web" / "js" / "i18n.js").read_text(encoding="utf-8")


def test_every_language_is_offered():
    offered = set(re.findall(r"\b([a-z]{2}): '", ENGINE.split("const LANGUAGES = {", 1)[1].split("};", 1)[0]))
    assert set(LANGUAGES) | {"fr"} == offered
    sys.path.insert(0, str(PROJECT / "src"))
    import config
    assert config.UI_LANGUAGES == offered


@pytest.mark.parametrize("lang", LANGUAGES)
def test_dictionary(lang):
    table = i18n.read_language(lang)
    catalog = set(json.loads(i18n.CATALOG.read_text(encoding="utf-8")))
    assert len(table) > 1000
    for source, target in table.items():
        assert source in catalog, f"{lang}: {source!r} is not in strings.json"
        assert isinstance(target, str) and target.strip()
        count = source.count("{}")
        for index in re.findall(r"\{(\d+)\}", target):
            assert 1 <= int(index) <= count, f"{lang}: {target!r} uses {{{index}}}"
        if count == 0:
            assert "{}" not in target, f"{lang}: {target!r} has a variable its source lacks"


def test_sentences_naming_a_file_or_a_plural_are_text():
    """« … finit par manifest.json. » and « {} fichier(s) » once stayed in French in every language."""
    for sentence in ("Collez l’adresse d’un addon : elle finit par manifest.json.",
                     "{} fichier(s) et {} torrent(s) supprimés du Cloud {}."):
        assert i18n.is_shown_text(sentence), sentence
    for code in ("manifest.json", "/js/app.js", "icons.svg#manga", "render(s)"):
        assert not i18n.is_shown_text(code), code


def test_server_messages_are_extracted():
    """The server's French reached the interface untranslated: « Google Drive KINO non détecté »."""
    source = '''
class MangaError(ValueError):
    pass

def status(name, size):
    if not name:
        return {"status": "disabled", "message": "Google Drive KINO non détecté"}
    if size > 10:
        raise MangaError(f"{name} inconnu.")
    if size > 5:
        raise ValueError("Paquet pour %s, Mac actuel : %s." % (name, size))
    send_json(error="Clé API {} manquante.".format(name) if size else "Nom manquant.")
    log.warning("Échec de lecture du cache")
    raise RuntimeError("mpv_create failed")
    return {"title": "Thrillers coréens", "error": "invalid hash"}
'''
    assert sorted(i18n.server_texts(source)) == [
        "Clé API {} manquante.", "Google Drive KINO non détecté", "Nom manquant.",
        "Paquet pour {}, Mac actuel : {}.", "{} inconnu.",
    ]


def test_server_messages_are_translated():
    catalog = set(json.loads(i18n.CATALOG.read_text(encoding="utf-8")))
    sent = {text for module in sorted(i18n.SRC.glob("*.py"))
            for text in i18n.server_texts(module.read_text(encoding="utf-8"))}
    assert "Google Drive KINO non détecté" in sent
    assert sent <= catalog, "run: python tools/i18n.py extract"
    for lang in LANGUAGES:
        table = i18n.read_language(lang)
        assert not sorted(sent - set(table)), f"{lang} lacks server messages"
