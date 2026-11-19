"""Build the committed local browser fork and verify its pinned wheel."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "docs/webskill-server-enhanced/local-browser-fork.json"


def main() -> None:
    pin = json.loads(MANIFEST.read_text(encoding="utf-8"))
    source = Path(os.environ.get("WEBSKILL_BROWSER_SOURCE", "../octop-browser-webskill"))
    source = (ROOT / source).resolve()
    commit = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    if commit != pin["commit"]:
        raise RuntimeError("The local browser fork HEAD does not match the pinned commit")
    if subprocess.check_output(
        ["git", "-C", str(source), "status", "--porcelain"], text=True
    ).strip():
        raise RuntimeError("The local browser fork must be clean before building the pinned wheel")
    timestamp = subprocess.check_output(
        ["git", "-C", str(source), "show", "-s", "--format=%ct", commit], text=True
    ).strip()
    uv = os.environ.get("UV", "uv")
    if shutil.which(uv) is None:
        raise RuntimeError("Set UV to the uv executable before building the pinned wheel")
    (ROOT / ".artifacts").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT / ".artifacts") as temporary:
        environment = dict(os.environ, SOURCE_DATE_EPOCH=timestamp)
        subprocess.run(
            [uv, "build", "--wheel", "--out-dir", temporary, str(source)],
            check=True,
            env=environment,
        )
        built = Path(temporary) / pin["wheel"]
        digest = hashlib.sha256(built.read_bytes()).hexdigest()
        if digest != pin["wheelSha256"]:
            raise RuntimeError("The rebuilt wheel SHA-256 does not match the pinned wheel")
        destination = ROOT / "wheelhouse" / pin["wheel"]
        destination.parent.mkdir(exist_ok=True)
        if not destination.exists():
            shutil.copyfile(built, destination)
        elif hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
            raise RuntimeError("The existing wheel does not match the pinned wheel")
    print(f"Verified {pin['package']}=={pin['version']} at {commit} ({digest})")


if __name__ == "__main__":
    main()
