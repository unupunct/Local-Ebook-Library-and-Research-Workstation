"""Locations of application data (never next to the executable by default)."""
from __future__ import annotations

import os
from pathlib import Path


def default_data_dir() -> Path:
    override = os.environ.get("ATLAS_DATA_DIR")
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "AtlasLibrary"


def bootstrap_settings_file() -> Path:
    """The settings file always lives in the default data dir so that the
    other locations (database, index, cache...) can be relocated from it."""
    return default_data_dir() / "settings.json"
