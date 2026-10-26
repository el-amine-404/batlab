"""First-page pictures of archived documents, for the web app's lists and pages.

Rendered from the clean copy (already validated: page images and an invisible text
layer) by pdftoppm, under CPU, memory and time limits, into ROOT/previews: beside
the archive, outside the folders people browse, and rebuildable at any time. Each
file names the document's version, so a new version gets new pictures. A failure
here never affects a document; its page shows no picture.
"""

from __future__ import annotations

import os
import resource
import subprocess
import tempfile
from pathlib import Path

SIZES = {"small": 320, "large": 1100}
TIMEOUT_SECONDS = 60


def _limits() -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
    resource.setrlimit(resource.RLIMIT_AS, (1 << 30, 1 << 30))
    os.nice(10)


def folder(root: Path) -> Path:
    path = Path(root) / "previews"
    path.mkdir(mode=0o755, exist_ok=True)
    return path


def names(document_id: str, version: int) -> dict[str, str]:
    return {size: f"{document_id}-v{int(version)}-{size}.png" for size in SIZES}


def render(root: Path, document_id: str, version: int, pdf: Path) -> bool:
    """Write the pictures for this version (if missing) and drop older versions'. True when both exist."""
    target = folder(root)
    wanted = names(document_id, version)
    for size, name in wanted.items():
        if (target / name).is_file():
            continue
        with tempfile.TemporaryDirectory(dir=target, prefix=".render-") as work:
            prefix = Path(work) / "page"
            try:
                subprocess.run(["pdftoppm", "-q", "-png", "-f", "1", "-l", "1", "-singlefile", "-scale-to-x", str(SIZES[size]),
                                "-scale-to-y", "-1", str(pdf), str(prefix)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=TIMEOUT_SECONDS, check=True, preexec_fn=_limits)
            except (OSError, subprocess.SubprocessError):
                return False
            produced = prefix.with_suffix(".png")
            if not produced.is_file() or produced.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
                return False
            os.chmod(produced, 0o644)
            os.replace(produced, target / name)
    for old in target.glob(f"{document_id}-v*.png"):
        if old.name not in wanted.values():
            old.unlink(missing_ok=True)
    return True


def remove(root: Path, document_id: str) -> int:
    removed = 0
    for path in folder(root).glob(f"{document_id}-v*.png"):
        path.unlink(missing_ok=True)
        removed += 1
    return removed


def backfill(archive) -> dict:
    """Pictures for every published document that has none (after an upgrade or a restore)."""
    made = failed = 0
    for document in archive.documents():
        try:
            sidecar = document.read()
            if not document.pdf.is_file():
                continue
            if all((folder(archive.root) / name).is_file() for name in names(sidecar["documentId"], sidecar["artifactVersion"]).values()):
                continue
            if render(archive.root, sidecar["documentId"], sidecar["artifactVersion"], document.pdf):
                made += 1
            else:
                failed += 1
        except (OSError, ValueError, KeyError):
            failed += 1
    return {"made": made, "failed": failed}
