# Vendored upstream provenance

This directory contains an **unmodified** copy of the Python package from:

- Repository: https://github.com/google-research/harness2
- Commit: `1cfe254e8d25`
- Version: `0.1.0`
- License: Apache-2.0 (see `LICENSE`)

Only the `harness2/` package, `LICENSE`, `README.md` and `pyproject.toml` were copied. Symbols
and assets from the upstream repository are intentionally absent.

The production installation path is a pinned editable/pip install of the upstream repository
(see README). This copy exists so tests run offline and so a checkout is self-contained.
morphd never edits files under this directory.

Verify with:

```sh
scripts/verify_vendor.sh
```
