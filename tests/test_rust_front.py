"""kino-server in front of the Python server (src/rust_front.py)."""
import os
import sys

import rust_front


def test_python_only_and_missing_program(monkeypatch):
    monkeypatch.setenv("KINO_PYTHON_ONLY", "1")
    assert rust_front.locate() is None
    monkeypatch.delenv("KINO_PYTHON_ONLY")
    monkeypatch.setenv("KINO_RUST_SERVER", "/nonexistent/kino-server")
    found = rust_front.locate()
    assert found is None or found.name == rust_front.NAME


def test_a_program_that_does_not_answer_is_not_kept(tmp_path):
    if sys.platform == "win32":
        return
    program = tmp_path / "kino-server"
    program.write_text("#!/bin/sh\nexit 0\n")
    os.chmod(program, 0o755)
    assert rust_front.start(program, 18999, 18998, tmp_path, "t" * 43) is None
    assert rust_front.start(tmp_path / "missing", 18999, 18998, tmp_path, "t" * 43) is None
    rust_front.stop(None)
