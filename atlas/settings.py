"""Persistent application settings (JSON)."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from .paths import bootstrap_settings_file, default_data_dir

log = logging.getLogger(__name__)

HOME_SECTIONS = [
    "continue_reading", "recently_added", "recently_read", "favorites",
    "recently_annotated", "popular_tags", "recent_notes", "statistics",
    "indexing_status", "offline_roots",
]


@dataclass
class Settings:
    first_run_done: bool = False
    # locations ("" = derive from data_dir)
    data_dir: str = ""
    db_dir: str = ""
    index_dir: str = ""
    cache_dir: str = ""
    ocr_dir: str = ""
    log_dir: str = ""
    backup_dir: str = ""
    # appearance / reader
    theme: str = "dark"
    library_view: str = "list"          # list | compact | grid
    reader_theme: str = "dark"          # light | sepia | dark
    reader_font_family: str = "Georgia"
    reader_font_size: int = 14
    reader_line_spacing: int = 150      # percent
    reader_paragraph_spacing: int = 8   # px
    reader_width: int = 760             # px, 0 = full
    reader_margin: int = 24
    reader_paginated: bool = False
    autosave_position: bool = True
    reading_stats: bool = True
    # search
    result_limit: int = 200
    group_by_book: bool = True
    passages_per_book: int = 5
    history_limit: int = 100
    # indexing
    index_threads: int = 2
    exclude_dirs: list[str] = field(default_factory=lambda: ["$RECYCLE.BIN", "System Volume Information", "node_modules", ".git"])
    exclude_patterns: list[str] = field(default_factory=lambda: ["~$*", "*.tmp", "*.part", "*.crdownload", "Thumbs.db"])
    skip_hidden: bool = True
    resume_on_start: bool = True
    scheduled_scan_minutes: int = 0     # 0 = off
    on_source_deleted: str = "mark"     # mark | ask_remove_index
    # OCR
    ocr_language: str = "eng"
    ocr_min_chars: int = 25
    tesseract_path: str = ""
    # backups
    backup_every_days: int = 7          # 0 = off
    backup_keep: int = 10
    backup_include_index: bool = False
    # startup / logging
    start_page: str = "home"
    reopen_last_book: bool = False
    log_level: str = "INFO"
    # metadata lookup (online, opt-in)
    online_metadata_enabled: bool = False
    # semantic search (opt-in)
    semantic_enabled: bool = False
    semantic_provider: str = ""
    semantic_model_path: str = ""
    # home
    home_sections: list[str] = field(default_factory=lambda: list(HOME_SECTIONS))

    # ---- derived locations -------------------------------------------------
    def _base(self) -> Path:
        return Path(self.data_dir) if self.data_dir else default_data_dir()

    def path_db_dir(self) -> Path:
        return Path(self.db_dir) if self.db_dir else self._base() / "db"

    def path_index_dir(self) -> Path:
        return Path(self.index_dir) if self.index_dir else self.path_db_dir()

    def path_cache_dir(self) -> Path:
        return Path(self.cache_dir) if self.cache_dir else self._base() / "cache"

    def path_ocr_dir(self) -> Path:
        return Path(self.ocr_dir) if self.ocr_dir else self._base() / "ocr"

    def path_log_dir(self) -> Path:
        return Path(self.log_dir) if self.log_dir else self._base() / "logs"

    def path_backup_dir(self) -> Path:
        return Path(self.backup_dir) if self.backup_dir else self._base() / "backups"

    def ensure_dirs(self) -> None:
        for p in (self.path_db_dir(), self.path_index_dir(), self.path_cache_dir(),
                  self.path_ocr_dir(), self.path_log_dir(), self.path_backup_dir()):
            p.mkdir(parents=True, exist_ok=True)

    # ---- persistence -------------------------------------------------------
    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        path = path or bootstrap_settings_file()
        s = cls()
        try:
            data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return s
        except (OSError, ValueError) as exc:
            log.warning("Could not read settings (%s); using defaults", exc)
            return s
        known = {f.name for f in fields(cls)}
        for k, v in data.items():
            if k in known:
                setattr(s, k, v)
        return s

    def save(self, path: Path | None = None) -> None:
        path = path or bootstrap_settings_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
