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
