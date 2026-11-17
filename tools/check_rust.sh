#!/bin/sh
# Everything the Rust workflow checks, in the same order; stops at the first failure.
set -e
cargo fmt --check
cargo clippy --quiet --all-targets --locked -- -D warnings
cargo test --quiet --locked
