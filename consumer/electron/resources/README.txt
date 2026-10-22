Place the signed one-file platform engine binary at resources/engine before packaging.
In CI, the release script copies packages/platform/<os>-<arch>/bin/morphd-engine here and
verifies its signature and SHA256. The application never downloads an engine at runtime.
