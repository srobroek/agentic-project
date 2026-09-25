# Rust recipes, imported by the root justfile.

# Format, lint, and test the rust code
[group('rust')]
rust: rust-fmt-check rust-lint rust-test

# Format
[group('rust')]
rust-fmt:
    cargo fmt --all

# Report formatting without changing a file. `just check` runs this one, because
# a task called check must not rewrite the tree it is checking: it cannot then be
# run on a dirty checkout, and in CI it reports success having edited the files.
# `just rust-fmt` is the writer.
[group('rust')]
rust-fmt-check:
    cargo fmt --all --check

# Clippy with warnings as errors
[group('rust')]
rust-lint:
    cargo clippy --all-targets --all-features -- -D warnings

# Test through nextest.
#
# `--no-tests=pass` because nextest exits 4 on an empty run, and `cargo init --bin`
# writes no test: a scaffold nobody has touched failed its own `just check`. Unlike
# Python, a seeded test would prove nothing here that `cargo build` does not.
[group('rust')]
rust-test:
    cargo nextest run --all-features --no-tests=pass

# Supply chain: advisories, licences, bans, sources
[group('rust')]
rust-deny:
    cargo deny check

# Unused dependencies
[group('rust')]
rust-machete:
    cargo machete

# Coverage
[group('rust')]
rust-cov:
    cargo llvm-cov nextest --all-features --no-tests=pass --lcov --output-path lcov.info

# Fetches into the shared cargo registry cache, so a warm run is 0.55s. `cargo build`
# would also fetch, but this separates a network failure from a compile failure.
# `cargo init` writes no lockfile, so the first setup creates it; every later run
# stays locked, and a stale lockfile is an error instead of a silent re-resolve.

# Fetch the locked dependencies
[group('rust')]
rust-install:
    @test -f Cargo.lock || cargo generate-lockfile
    cargo fetch --locked
