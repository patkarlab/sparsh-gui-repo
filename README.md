# SPARSH — ONT Run Manager

Cross-platform desktop GUI (Windows, Linux, macOS) for real-time Oxford Nanopore run
monitoring and SPARSH methylation analysis.

## Project structure

```
sparsh-gui/
├── main.py                  # Entry point — run this
├── requirements.txt
├── environment.yml          # Conda env for the GUI app
├── sparsh_config.json       # Auto-created on first run
└── sparsh/
    ├── __init__.py
    ├── config.py            # Load / save JSON config
    ├── state.py             # Shared in-memory state (thread-safe)
    ├── watcher.py           # BAM folder filesystem watcher
    ├── runner.py            # SPARSH subprocess manager
    └── gui.py               # CustomTkinter main window
```

## Setup

### Option A — pip

```bash
pip install customtkinter watchdog
python main.py
```

### Option B — conda (recommended)

```bash
conda env create -f environment.yml
conda activate sparsh-gui
python main.py
```

## First-time use

1. Set Run name, BAM root, and Output root in the top bar → click Apply
2. Add samples (name + barcode + sex) in the left panel
3. Fill in Global config (reference, manifest, model dir, CN panel) → Save config

## How the watcher works

Every `watch_interval` seconds the app scans each sample's barcode subfolder.
When a BAM file size stops changing for `bam_stable_wait` seconds, SPARSH fires automatically.
Throughput (GB/hr) is computed from consecutive scan size deltas.

## Packaging

```bash
pip install pyinstaller
pyinstaller --onedir --windowed --name "SPARSH Run Manager" main.py
```
