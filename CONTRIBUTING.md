# Contributing

Bug reports, ideas and pull requests are welcome.

## Run KINO from source

KINO is written in Rust (`crates/`); install Rust with [rustup](https://rustup.rs),
the version in `rust-toolchain.toml` comes by itself.

```bash
cargo run -p kino-desktop
```

## Tests

```bash
bash tools/check_rust.sh            # formatting, lints, Rust tests
node --test tests/test_*.js
node tools/frontend_check.js
```

The tests never touch your own data: they use temporary folders. Run KINO from
source with `HOME` set to a temporary folder to keep your own data out of it.

## Guidelines

- The interface is written in French. A new text needs its translations: run
  `python tools/i18n.py extract`, then add it to `web/js/i18n/<language>.js`.
- Keep changes small and tested. A pull request says what changes and how it was
  tried.
- Commit messages are short, in English, and say what changes.
- To build the apps or publish a version, see [packaging/README.md](packaging/README.md).
