from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    database_url: str
    incoming: Path
    work: Path
    exports: Path
    static: Path
    port: int
    clamd_host: str
    clamd_port: int
    public_url: str


def load() -> Config:
    root = Path(os.getenv("SCANLOOM_STORAGE", "/var/lib/scanloom"))
    return Config(
        database_url=os.environ["SCANLOOM_DATABASE_URL"],
        incoming=Path(os.getenv("SCANLOOM_INCOMING", root / "incoming")),
        work=Path(os.getenv("SCANLOOM_WORK", root / "work")),
        exports=Path(os.getenv("SCANLOOM_EXPORTS", root / "exports")),
        static=Path(os.getenv("SCANLOOM_STATIC", "/opt/scanloom/static")),
        port=int(os.getenv("SCANLOOM_PORT", "8850")),
        clamd_host=os.getenv("SCANLOOM_CLAMD_HOST", "clamav"),
        clamd_port=int(os.getenv("SCANLOOM_CLAMD_PORT", "3310")),
        public_url=os.getenv("SCANLOOM_PUBLIC_URL", "http://scanloom.homelab.lan").rstrip("/"),
    )
