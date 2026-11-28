#!/usr/bin/env bash
# Stage the freshly built portable zip + manifest into desktop resources,
# mirroring the "Stage Electron runtime" step of .github/workflows/octop-desktop.yml.
set -euo pipefail

cd "$(dirname "$0")/../.."
plat="${1:?plat}"
ver="${2:?version}"
zip="desktop/portable/release/Octop-portable-${plat}-${ver}.zip"

test -f "$zip"
test -f "desktop/portable/release/Octop-${plat}/packages/octop/dashboard/index.html"

rm -rf desktop/src/resources/portable
mkdir -p desktop/src/resources/portable
cp "$zip" desktop/src/resources/portable/

python - "$zip" "$ver" <<'PY'
import hashlib
import json
import pathlib
import sys
import zipfile

path = pathlib.Path(sys.argv[1])
if not zipfile.is_zipfile(path):
    raise SystemExit(f"not a portable zip: {path}")
manifest = {
    "version": sys.argv[2],
    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
}
dest = pathlib.Path("desktop/src/resources/portable/portable-version.json")
dest.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
print(f"Bundled {path.name} with SHA-256 {manifest['sha256'][:12]}…")
PY

echo "[stage] done: desktop/src/resources/portable/"
ls -la desktop/src/resources/portable/
