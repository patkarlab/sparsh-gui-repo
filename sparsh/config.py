"""
config.py — the settings: defaults, descriptions shown in the window, and loading
and saving to a JSON file.

The file lives in the user's configuration folder, ~/.config/sparsh-run-manager/
sparsh_config.json (or $XDG_CONFIG_HOME/sparsh-run-manager/), so it survives
reinstalling or uninstalling the app. Older versions kept it next to main.py
(inside _internal/ for the packaged app); such a file is still read when the new
location has none, and is written to the new location the first time Save is pressed.
"""
import json
import os
import sys
from pathlib import Path
from typing import List

APP_ID = "sparsh-run-manager"
CONFIG_FILENAME = "sparsh_config.json"

CONDA_ROOT_NAMES = ("anaconda3", "miniconda3", "miniforge3", "mambaforge", "conda")
CONDA_SYSTEM_ROOTS = ("/opt/anaconda3", "/opt/miniconda3", "/opt/miniforge3", "/opt/conda",
                      "/usr/local/anaconda3", "/usr/local/miniconda3")


def conda_sh_candidates() -> List[Path]:
    """Where conda.sh usually is, most likely first."""
    found: List[Path] = []
    exe = os.environ.get("CONDA_EXE", "")
    if exe:                               # <root>/bin/conda -> <root>/etc/profile.d/conda.sh
        found.append(Path(exe).resolve().parent.parent / "etc" / "profile.d" / "conda.sh")
    for name in CONDA_ROOT_NAMES:
        found.append(Path.home() / name / "etc" / "profile.d" / "conda.sh")
    for root in CONDA_SYSTEM_ROOTS:
        found.append(Path(root) / "etc" / "profile.d" / "conda.sh")
    return found


def find_conda_sh() -> str:
    """The first conda.sh that exists, else the Anaconda default."""
    for candidate in conda_sh_candidates():
        if candidate.is_file():
            return str(candidate)
    return str(Path.home() / "anaconda3" / "etc" / "profile.d" / "conda.sh")


DEFAULTS = {
    # ── SPARSH installation ────────────────────────────────────────
    "sparsh_location": "",       # the SPARSH repository folder (scripts/ inside it)
    "reference_fasta": "",       # blank: found inside <SPARSH folder>/references/
    "epic_manifest":   "",       # blank: <SPARSH folder>/references/EPIC-8v2-0_A2.csv
    "model_dir":       "",       # blank: <SPARSH folder>/sparsh_model_<version>/

    # ── Conda environment ──────────────────────────────────────────
    "conda_env": "sparsh",
    "conda_sh":  find_conda_sh(),

    # ── Analysis options ───────────────────────────────────────────
    "panel":         "",         # blank: --no_panel
    "blast_percent": "",         # blank: --blast_percent left out
    "aligner":       "dorado",   # dorado or minimap2, for reads that need aligning
    "threads":       8,          # --threads of run_sample.py
    "device":        "cuda",     # cuda or cpu, for the predictions (cuda falls back to cpu)
    "extra_args":    "",         # further options appended to the run_sample.py command

    # ── Watcher behaviour ──────────────────────────────────────────
    "watch_interval":  30,       # seconds between scans of the barcode folders
    "bam_stable_wait": 60,       # seconds a BAM file must stay unchanged to count as finished

    # ── Window ─────────────────────────────────────────────────────
    "appearance": "System",      # System, Light or Dark

    # ── Remembered from the last Start ─────────────────────────────
    "last_run_folder":   "",
    "last_local_output": "",
    "last_lts_output":   "",
}

DESCRIPTIONS = {
    "sparsh_location":
        "The SPARSH repository folder: the one that holds scripts/run_sample.py, scripts/predict.py "
        "and scripts/report.py. The three files below are looked for inside it when their boxes "
        "are left blank.",
    "reference_fasta":
        "GRCh37 (hg19) reference FASTA with its .fai index next to it. Blank: the first of "
        "hg19_all.fasta, hg19_main.fa, hg19.fa or hg19.fasta in <SPARSH folder>/references/, "
        "or the only FASTA file there.",
    "epic_manifest":
        "Illumina's MethylationEPIC v2.0 manifest, EPIC-8v2-0_A2.csv. "
        "Blank: <SPARSH folder>/references/EPIC-8v2-0_A2.csv, or the only EPIC*.csv there.",
    "model_dir":
        "The model folder from Zenodo, for example sparsh_model_0.4.0. "
        "Blank: the only sparsh_model_* folder inside the SPARSH folder.",
    "conda_env":
        "The conda environment SPARSH runs in, activated before every command. It must provide "
        "samtools and modkit, and minimap2 or dorado for reads that are not aligned yet.",
    "conda_sh":
        "The conda.sh of your conda installation, used to activate the environment. It is "
        "<conda folder>/etc/profile.d/conda.sh; the box is filled in automatically when one of "
        "the usual installations is found.",
    "panel":
        "A stored copy-number reference made with scripts/karyotype.py panel, passed as --panel. "
        "Blank: --no_panel, which calls copy number with GC correction only.",
    "blast_percent":
        "Leukemic blast percentage used for samples whose own Blast % is blank: above 0 and at "
        "most 100, decimals allowed. The copy-number call keeps the leukemic fraction within 20 "
        "points of it. Blank: --blast_percent is left out for those samples.",
    "aligner":
        "Used only for BAM files that are not aligned to the reference (or aligned to another "
        "one). dorado is SPARSH's default and is not installed by its conda environment; "
        "minimap2 is. Give the path of dorado in the extra options when it is not on the PATH.",
    "threads":
        "Threads for alignment, sorting and the methylation pileup (--threads of run_sample.py). "
        "Samples run one at a time, so this can be most of the machine's cores.",
    "device":
        "Device for the predictions, --device of run_sample.py and predict.py. cuda uses the "
        "graphics card and falls back to the processor when none is available; cpu never uses it.",
    "extra_args":
        "Further options appended to every run_sample.py command, as they would be typed in a "
        "terminal, for example --dorado /opt/ont/dorado/bin/dorado --strands forward.",
    "watch_interval":
        "Seconds between scans of the barcode folders.",
    "bam_stable_wait":
        "Seconds a BAM file without a .bai index must stay unchanged before it counts as "
        "finished. Files with an index are used as soon as the index appears.",
    "appearance":
        "Light or dark window, or whatever the desktop uses.",
}


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP_ID


def config_path() -> Path:
    return config_dir() / CONFIG_FILENAME


def legacy_config_path() -> Path:
    """Where versions before 1.1 kept the file: next to main.py, or _internal/ when packaged."""
    return Path(__file__).resolve().parent.parent / CONFIG_FILENAME


def load() -> dict:
    cfg = dict(DEFAULTS)
    for path in (config_path(), legacy_config_path()):
        if not path.is_file():
            continue
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                cfg.update({k: v for k, v in data.items() if k in DEFAULTS})
        except (OSError, ValueError) as exc:
            print(f"[config] {path} could not be read ({exc}); using defaults", file=sys.stderr)
        break
    return cfg


def save(cfg: dict) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({k: cfg.get(k, v) for k, v in DEFAULTS.items()}, fh, indent=2)
        fh.write("\n")
