"""Rotating file logging. Document contents are never logged."""
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path


def setup_logging(log_dir: Path, level: str = "INFO") -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    logfile = log_dir / "atlas.log"
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "_atlas", False):
            root.removeHandler(h)
    fh = logging.handlers.RotatingFileHandler(logfile, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(threadName)s %(name)s: %(message)s"))
    fh._atlas = True  # type: ignore[attr-defined]
    root.addHandler(fh)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    return logfile
