"""Keep the PRD file the user imported, so later steps can re-read it.

The extracted text is not enough on its own: images cannot travel as text, and
a layout bug is only fixable if the original file is still around. The bytes
never enter the project brief — that would push a multi-megabyte blob through
every Agent request — they stay on disk behind a short id.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

MAX_STORED_BYTES = 24 * 1024 * 1024


def root() -> Path:
    configured = os.getenv("FIRSTFLIGHT_IMPORTED_PRD_ROOT")
    base = Path(configured) if configured else Path(__file__).resolve().parents[2] / "data" / "imported-prd"
    base.mkdir(parents=True, exist_ok=True)
    return base


def save(content: bytes) -> str:
    """Store the file and return the id that stands in for it."""

    if not content or len(content) > MAX_STORED_BYTES:
        raise ValueError("PRD 文件不能超过 24 MB")
    import_id = hashlib.sha256(content).hexdigest()[:32]
    path = root() / f"{import_id}.docx"
    if not path.exists():
        path.write_bytes(content)
    return import_id


def load(import_id: str | None) -> bytes | None:
    """The stored file, or ``None`` when the id is unknown or unusable."""

    if not import_id or not import_id.isalnum() or len(import_id) > 64:
        return None
    path = root() / f"{import_id}.docx"
    if not path.is_file():
        return None
    return path.read_bytes()
