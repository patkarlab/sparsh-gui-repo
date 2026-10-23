"""
config.py
Loads and saves the global SPARSH configuration to a JSON file
alongside the app (or in the user's home dir as a fallback).
"""

import json
import os
from pathlib import Path

CONFIG_FILENAME = "sparsh_config.json"

DEFAULTS = {
    "bam_root": "",
    "output_root": "",
    "reference": "hg19_main.fa",
    "manifest": "EPIC-8v2-0_A2.csv",
    "model_dir": "sparsh_model_0.4.0",
    "panel": "cn_panel.tsv.gz",
    "blast_percent": "80",
    "scripts_dir": "scripts",         # directory containing run_sample.py
    "watch_interval": 30,             # seconds between BAM folder scans
    "bam_stable_wait": 60,            # seconds a BAM must be unchanged before triggering analysis
}


def _config_path() -> Path:
    """Return the path to the config file, preferring the app directory."""
    app_dir = Path(__file__).parent.parent
    candidate = app_dir / CONFIG_FILENAME
    if candidate.parent.exists():
        return candidate
    # Fallback: user home
    return Path.home() / CONFIG_FILENAME


def load() -> dict:
    """Load config from disk. Missing keys are filled from DEFAULTS."""
    path = _config_path()
    cfg = dict(DEFAULTS)
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                on_disk = json.load(f)
            cfg.update(on_disk)
        except Exception:
            pass  # corrupt file — use defaults
    return cfg


def save(cfg: dict) -> None:
    """Persist config to disk."""
    path = _config_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
