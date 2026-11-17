# Contributing

Bug reports, ideas and pull requests are welcome.

## Run KINO from source

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt pytest
.venv/bin/python src/desktop.py
```

## Tests

```bash
.venv/bin/python -m pytest -q tests
node --test tests/test_*.js
node tools/frontend_check.js
```

The tests never touch your own data: they use temporary folders. Run any script
that imports KINO's modules with `HOME` set to a temporary folder for the same
reason.

## Guidelines

- The interface is written in French. A new text needs its translations: run
  `python tools/i18n.py extract`, then add it to `web/js/i18n/<language>.js`.
- Keep changes small and tested. A pull request says what changes and how it was
  tried.
- Commit messages are short, in English, and say what changes.
- To build the apps or publish a version, see [packaging/README.md](packaging/README.md).
