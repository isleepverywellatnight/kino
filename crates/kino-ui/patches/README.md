# Patched dependencies

- `xattr-0.2.3`: xattr 0.2.3 (MIT/Apache-2.0, licenses kept), reached through
  gpui's HTTP client (zed-async-tar). It reads `libc::ENOATTR`, which libc no
  longer exports on Linux and Android since 0.2.178; there it was always
  `ENODATA`. The `kino` crate needs a recent libc (librqbit → rlimit wants
  >= 0.2.180), so `src/sys/mod.rs` uses `ENODATA` on those systems. Nothing
  else changes. Remove the patch once gpui moves to xattr 1.x.
