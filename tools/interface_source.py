"""Python twin of tools/interface_source.js: the interface's main code, its
parts joined in the order web/index.html loads them."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def app_parts(root=ROOT):
    html = (Path(root) / "web" / "index.html").read_text(encoding="utf-8")
    return re.findall(r'<script src="/js/(app-[\w-]+\.js)"></script>', html)


def app_source(root=ROOT):
    return "".join((Path(root) / "web" / "js" / name).read_text(encoding="utf-8") for name in app_parts(root))
