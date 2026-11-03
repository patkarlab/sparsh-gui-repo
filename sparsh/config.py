"""
config.py — loads/saves global SPARSH config to JSON.
"""
import json
from pathlib import Path

CONFIG_FILENAME = "sparsh_config.json"

DEFAULTS = {
    # ── Core: the one folder that contains everything ──────────
    "sparsh_location": "",   # e.g. /mnt/14F45E80F45E6450/pipelines/sparsh

    # ── Conda environment ──────────────────────────────────────
    "conda_env":      "sparsh",
    "conda_sh":       str(Path.home() / "anaconda3/etc/profile.d/conda.sh"),

    # ── Analysis options ───────────────────────────────────────
    "blast_percent":  "",      # leave blank to omit --blast_percent
    "panel":          "",      # leave blank to use --no_panel (default)

    # ── Watcher behaviour ──────────────────────────────────────
    "watch_interval":  30,    # seconds between BAM folder scans
    "bam_stable_wait": 60,    # seconds a BAM must be unchanged before triggering

    # ── Remembered paths ──────────────────────────────────────
    "last_run_folder":    "",
    "last_local_output":  "",
    "last_lts_output":    "",
}

DESCRIPTIONS = {
    "sparsh_location":
        "Root folder of your SPARSH installation. Must contain:\n"
        "  scripts/run_sample.py\n"
        "  references/hg19_all.fasta\n"
        "  references/EPIC-8v2-0_A2.csv\n"
        "  sparsh_model_0.4.0/\n"
        "  results/  (output written here automatically)",
    "conda_env":
        "Name of the conda environment to activate before running. Default: sparsh",
    "conda_sh":
        "Path to conda.sh — used to activate conda in the shell. "
        "Default: ~/anaconda3/etc/profile.d/conda.sh\n"
        "Change to ~/miniconda3/etc/profile.d/conda.sh if using Miniconda.",
    "blast_percent":
        "Minimum BLAST % identity threshold (integer). Leave blank to omit --blast_percent entirely.",
    "panel":
        "Path to CN panel file (e.g. cn_panel.tsv.gz). "
        "Leave blank to use --no_panel (default). "
        "If a path is entered here, --panel <path> is passed instead.",
    "watch_interval":
        "How often (seconds) the app scans each barcode folder for new BAM files. Default: 30",
    "bam_stable_wait":
        "Seconds a BAM file size must be unchanged before analysis is triggered. Default: 60",
}


def _config_path() -> Path:
    app_dir = Path(__file__).parent.parent
    candidate = app_dir / CONFIG_FILENAME
    if candidate.parent.exists():
        return candidate
    return Path.home() / CONFIG_FILENAME


def load() -> dict:
    path = _config_path()
    cfg  = dict(DEFAULTS)
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    return cfg


def save(cfg: dict) -> None:
    path = _config_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
