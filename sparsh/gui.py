"""
gui.py — the SPARSH Run Manager window.

Everything that touches widgets runs on the Tk main thread: the watcher and the
runner only write to app_state, and _refresh_gui() reads it every two seconds.
"""
import csv
import os
import re
import shlex
import subprocess
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import List, Optional, Tuple

import customtkinter as ctk

from . import APP_NAME, __version__
from . import config as cfg_module
from . import paths as sparsh_paths
from .runner import (highest_iteration, output_name, output_roots, recorded_run, runner,
                     subprocess_env)
from .state import SampleStatus, app_state, normalise_barcode
from .watcher import BamWatcher

ctk.set_default_color_theme("blue")

# ── colours (light, dark) ─────────────────────────────────────────────────────
MUTED     = ("gray40", "gray65")
PANEL     = ("gray92", "gray17")
ROW       = ("gray90", "gray20")
ROW_HOVER = ("gray85", "gray25")
ROW_SEL   = ("#D6E4F5", "#2B3F58")
GREEN, GREEN_DARK = "#3F9B4A", "#2F7A38"
RED,   RED_DARK   = "#D9433F", "#A32D2D"
AMBER, BLUE       = "#D9962B", "#3B82D6"
GREY              = "#8A8A8A"

STATUS_COLORS = {
    SampleStatus.WAITING:    GREY,
    SampleStatus.SEQUENCING: "#6C8EBF",
    SampleStatus.QUEUED:     AMBER,
    SampleStatus.ANALYSING:  GREEN,
    SampleStatus.DONE:       BLUE,
    SampleStatus.ERROR:      RED,
}
STATUS_LABELS = {
    SampleStatus.WAITING:    "Waiting",
    SampleStatus.SEQUENCING: "Sequencing",
    SampleStatus.QUEUED:     "Queued",
    SampleStatus.ANALYSING:  "Analysing",
    SampleStatus.DONE:       "Done",
    SampleStatus.ERROR:      "Error",
}
SAMPLE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")   # as run_sample.py requires
OUTPUT_NAME_RE = re.compile(r"[A-Za-z0-9._-]+")            # one folder name: no spaces or slashes
SEX_NONE = "Not set"
SAMPLE_LIST_FILE = "run_manager_samples.csv"               # written at Start, for Import

COLUMNS = [("Sample", 112), ("Barcode", 76), ("Sex", 36), ("Blast %", 54),
           ("Max runs", 62), ("Done", 44), ("GB/hr", 50), ("Status", 84)]


def _elapsed(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min {seconds % 60:02d} s"
    return f"{seconds // 3600} h {(seconds % 3600) // 60:02d} min"


def validate_sample(name: str, barcode_raw: str, sex: str, blast: str,
                    repeats: str) -> Tuple[Optional[str], tuple]:
    """Check one sample's fields. Returns (error message or None, normalised values)."""
    name, barcode_raw, sex, blast, repeats = (x.strip() for x in
                                              (name, barcode_raw, sex, blast, repeats))
    if not name or not barcode_raw:
        return "Sample and Barcode are required.", ()
    if not SAMPLE_ID_RE.fullmatch(name):
        return ("Sample names can only use letters, numbers, '.', '_' and '-', "
                "and must start with a letter or number."), ()
    barcode = normalise_barcode(barcode_raw)
    if barcode is None:
        return f"Barcode must be a number from 01 to 99 (not '{barcode_raw}').", ()
    sex = sex.upper()
    if sex in ("", SEX_NONE.upper(), "-", "—"):
        sex = ""
    elif sex in ("FEMALE", "F"):
        sex = "F"
    elif sex in ("MALE", "M"):
        sex = "M"
    else:
        return f"Sex must be F or M, or blank (not '{sex}').", ()
    if blast:
        try:
            value = float(blast.rstrip("%"))
        except ValueError:
            value = -1.0
        if not 0 < value <= 100:
            return "Blast % must be a number above 0 and at most 100.", ()
        blast = f"{value:g}"
    try:
        max_rep = int(repeats or "0")
        if max_rep < 0:
            raise ValueError
    except ValueError:
        return "Max runs must be a whole number (0 = unlimited).", ()
    return None, (name, barcode, sex, blast, max_rep)


class App(ctk.CTk):
    def __init__(self):
        self._cfg = cfg_module.load()
        ctk.set_appearance_mode(str(self._cfg.get("appearance") or "System"))
        super().__init__()
        self.title(f"{APP_NAME} {__version__}")
        width = min(1180, self.winfo_screenwidth() - 40)
        height = max(640, min(920, self.winfo_screenheight() - 80))
        self.geometry(f"{width}x{height}")
        self.minsize(980, 640)

        self.f_body   = ctk.CTkFont(size=12)
        self.f_small  = ctk.CTkFont(size=11)
        self.f_tiny   = ctk.CTkFont(size=10)
        self.f_bold   = ctk.CTkFont(size=12, weight="bold")
        self.f_title  = ctk.CTkFont(size=13, weight="bold")
        self.f_mono   = ctk.CTkFont(size=11, family="monospace")
        self.f_mono_s = ctk.CTkFont(size=10, family="monospace")
        self.f_metric = ctk.CTkFont(size=18, weight="bold")

        self._selected_sample: Optional[str] = None
        self._watcher: Optional[BamWatcher] = None
        self._sample_rows: dict = {}
        self._running = False
        self._run_cfg: dict = {}          # settings captured when Start is pressed
        self._sig_active = None           # change signatures: panels are only
        self._sig_done = None             # rebuilt when their content changes
        self._sig_bams = None
        self._tb_cache: dict = {}
        self._started_outputs: set = set()   # output folders already confirmed this session

        self._build_ui()
        if not str(self._cfg.get("sparsh_location", "")).strip():
            # First start: the SPARSH folder is needed before anything else. Switched
            # after the first draw, because CTkTabview finishes a switch 100 ms later.
            self.after(200, lambda: self._tabs.set("Settings"))
        self._start_refresh_loop()

    # ═════════════════════════════════════════════════════════════
    # UI BUILD
    # ═════════════════════════════════════════════════════════════

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._build_topbar()

        self._tabs = ctk.CTkTabview(self, corner_radius=0)
        self._tabs.grid(row=1, column=0, sticky="nsew")
        self._tabs.add("Run")
        self._tabs.add("Settings")
        self._build_run_tab(self._tabs.tab("Run"))
        self._build_config_tab(self._tabs.tab("Settings"))

        self._build_statusbar()

    # ── Top bar ───────────────────────────────────────────────────

    def _build_topbar(self):
        bar = ctk.CTkFrame(self, corner_radius=0)
        bar.grid(row=0, column=0, sticky="ew")
        bar.grid_columnconfigure(1, weight=3)   # run folder entry expands most
        bar.grid_columnconfigure(4, weight=2)   # local output entry
        bar.grid_columnconfigure(7, weight=2)   # long-term storage entry

        def path_field(column, label, var, title, first=False):
            ctk.CTkLabel(bar, text=label, font=self.f_body
                         ).grid(row=0, column=column, padx=(14 if first else 0, 6),
                                pady=(12, 6), sticky="w")
            ctk.CTkEntry(bar, textvariable=var, font=self.f_mono
                         ).grid(row=0, column=column + 1, padx=(0, 4), pady=(12, 6), sticky="ew")
            ctk.CTkButton(bar, text="Browse", width=64, font=self.f_small,
                          command=lambda: self._browse_folder(var, title)
                          ).grid(row=0, column=column + 2, padx=(0, 14), pady=(12, 6))

        self._run_folder_var = tk.StringVar(value=self._cfg.get("last_run_folder", ""))
        self._local_out_var = tk.StringVar(value=self._cfg.get("last_local_output", ""))
        self._lts_out_var = tk.StringVar(value=self._cfg.get("last_lts_output", ""))
        path_field(0, "Run folder", self._run_folder_var, "Select the MinKNOW run folder", True)
        path_field(3, "Local output", self._local_out_var, "Select the local output folder")
        path_field(6, "Long-term storage", self._lts_out_var,
                   "Select the long-term storage folder (optional)")

        # Output folder name (default: today's date) — holds the iteration folders
        ctk.CTkLabel(bar, text="Output folder name", font=self.f_body
                     ).grid(row=1, column=0, padx=(14, 6), pady=(0, 12), sticky="w")
        name_row = ctk.CTkFrame(bar, fg_color="transparent")
        name_row.grid(row=1, column=1, columnspan=8, padx=(0, 14), pady=(0, 12), sticky="ew")
        self._out_name_var = tk.StringVar(value=time.strftime("%d_%m_%Y"))
        ctk.CTkEntry(name_row, textvariable=self._out_name_var, width=160,
                     font=self.f_mono).pack(side="left")
        self._out_hint = ctk.CTkLabel(name_row, text="", font=self.f_tiny,
                                      text_color=MUTED, anchor="w")
        self._out_hint.pack(side="left", padx=(10, 6))
        ctk.CTkButton(name_row, text="Open", width=52, height=24, font=self.f_tiny,
                      fg_color="transparent", border_width=1, text_color=("gray10", "gray90"),
                      command=self._open_output_root).pack(side="left")
        for var in (self._out_name_var, self._local_out_var, self._lts_out_var):
            var.trace_add("write", lambda *_: self._update_output_hint())
        self._update_output_hint()

    # ── Run tab ───────────────────────────────────────────────────

    def _build_run_tab(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_columnconfigure(1, weight=1)
        parent.grid_rowconfigure(0, weight=1)

        # ══ LEFT PANEL: samples ═══════════════════════════════════
        left = ctk.CTkFrame(parent, corner_radius=8)
        left.grid(row=0, column=0, sticky="nsew", padx=(10, 5), pady=(4, 10))
        left.grid_rowconfigure(2, weight=1)
        left.grid_columnconfigure(0, weight=1)

        title_row = ctk.CTkFrame(left, fg_color="transparent")
        title_row.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        title_row.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(title_row, text="Samples", font=self.f_title
                     ).grid(row=0, column=0, sticky="w")
        self._start_btn = ctk.CTkButton(
            title_row, text="Start Analysis", width=118, font=self.f_body,
            fg_color=GREEN, hover_color=GREEN_DARK, command=self._start_analysis)
        self._start_btn.grid(row=0, column=1, padx=(6, 4))
        self._stop_btn = ctk.CTkButton(
            title_row, text="Stop Analysis", width=118, font=self.f_body,
            fg_color=GREY, hover_color=RED, command=self._stop_analysis)
        self._stop_btn.grid(row=0, column=2, padx=(0, 2))
        self._stop_btn.configure(state="disabled")

        hdr = ctk.CTkFrame(left, fg_color="transparent")
        hdr.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 2))
        for col, w in COLUMNS:
            ctk.CTkLabel(hdr, text=col, font=self.f_tiny, text_color=MUTED,
                         width=w, anchor="w").pack(side="left", padx=1)

        self._sample_scroll = ctk.CTkScrollableFrame(left, corner_radius=0)
        self._sample_scroll.grid(row=2, column=0, sticky="nsew", padx=10, pady=2)
        self._empty_hint = ctk.CTkLabel(
            self._sample_scroll, font=self.f_small, text_color=MUTED, justify="left",
            text="No samples yet. Add each sample with its barcode number below, "
                 "or import a list.")
        self._empty_hint.pack(anchor="w", padx=6, pady=12)

        # ── Add sample form ───────────────────────────────────────
        add_outer = ctk.CTkFrame(left, corner_radius=6)
        add_outer.grid(row=3, column=0, sticky="ew", padx=10, pady=(6, 10))
        add_outer.grid_columnconfigure(0, weight=1)

        add_title = ctk.CTkFrame(add_outer, fg_color="transparent")
        add_title.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 2))
        add_title.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(add_title, text="Add sample", font=self.f_bold, text_color=MUTED
                     ).grid(row=0, column=0, sticky="w")
        for col, text, cmd in ((1, "Import list…", self._import_samples),
                               (2, "Export list…", self._export_samples)):
            ctk.CTkButton(add_title, text=text, width=92, height=24, font=self.f_tiny,
                          fg_color="transparent", border_width=1,
                          text_color=("gray10", "gray90"), command=cmd
                          ).grid(row=0, column=col, padx=(4, 0))

        form = ctk.CTkFrame(add_outer, fg_color="transparent")
        form.grid(row=1, column=0, sticky="w", padx=10, pady=(4, 4))

        self._new_sample_var  = tk.StringVar()
        self._new_barcode_var = tk.StringVar()
        self._new_sex_var     = tk.StringVar(value=SEX_NONE)
        self._new_blast_var   = tk.StringVar(value="")
        self._new_repeats_var = tk.StringVar(value="0")
        self._add_err_var     = tk.StringVar()

        def field(column, label, make_input):
            box = ctk.CTkFrame(form, fg_color="transparent")
            box.grid(row=0, column=column, sticky="sw", padx=(0, 6))
            ctk.CTkLabel(box, text=label, font=self.f_tiny, text_color=MUTED, anchor="w"
                         ).pack(anchor="w")
            widget = make_input(box)
            widget.pack(anchor="w", pady=(0, 4))
            return widget

        self._new_sample_entry = field(0, "Sample  *", lambda p: ctk.CTkEntry(
            p, textvariable=self._new_sample_var, width=120, font=self.f_body))
        barcode_entry = field(1, "Barcode  *  (01–99)", lambda p: ctk.CTkEntry(
            p, textvariable=self._new_barcode_var, width=64, font=self.f_body))
        field(2, "Sex", lambda p: ctk.CTkOptionMenu(
            p, variable=self._new_sex_var, values=[SEX_NONE, "F", "M"], width=84,
            font=self.f_body))
        blast_entry = field(3, "Blast %", lambda p: ctk.CTkEntry(
            p, textvariable=self._new_blast_var, width=60, font=self.f_body))
        repeats_entry = field(4, "Max runs", lambda p: ctk.CTkEntry(
            p, textvariable=self._new_repeats_var, width=60, font=self.f_body))
        ctk.CTkButton(form, text="+ Add", width=72, font=self.f_body,
                      command=self._add_sample).grid(row=0, column=5, sticky="s", pady=(0, 4))
        for entry in (self._new_sample_entry, barcode_entry, blast_entry, repeats_entry):
            entry.bind("<Return>", lambda _e: self._add_sample())

        ctk.CTkLabel(add_outer, font=self.f_tiny, text_color=MUTED, anchor="w",
                     justify="left", wraplength=520,
                     text="* Required. Sex (F or M) and the leukemic blast percentage go to the "
                          "copy-number call; a blank Blast % uses the default from Settings. "
                          "Max runs stops the sample after that many analyses (0 = no limit). "
                          "Enter adds the sample."
                     ).grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 6))
        ctk.CTkLabel(add_outer, textvariable=self._add_err_var, text_color=RED,
                     font=self.f_small, anchor="w", justify="left", wraplength=520
                     ).grid(row=3, column=0, sticky="w", padx=10, pady=(0, 6))

        # ══ RIGHT PANEL: progress ═════════════════════════════════
        right = ctk.CTkFrame(parent, corner_radius=8)
        right.grid(row=0, column=1, sticky="nsew", padx=(5, 10), pady=(4, 10))
        right.grid_columnconfigure(0, weight=1)
        for row, share in ((1, 3), (3, 2), (5, 2), (13, 4)):   # boxes share the height
            right.grid_rowconfigure(row, weight=share)

        def heading(text, row, pady=(4, 2)):
            lbl = ctk.CTkLabel(right, text=text, font=self.f_bold)
            lbl.grid(row=row, column=0, padx=14, pady=pady, sticky="w")
            return lbl

        # Currently analysing: a header that updates in place, and rows rebuilt on change
        heading("Currently analysing", 0, pady=(12, 2))
        self._active_frame = ctk.CTkScrollableFrame(right, corner_radius=4, height=96)
        self._active_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 4))
        self._active_frame.grid_columnconfigure(0, weight=1)
        self._active_header = ctk.CTkLabel(self._active_frame, text="", anchor="w",
                                           justify="left", wraplength=460, font=self.f_small)
        self._active_header.pack(fill="x", padx=8, pady=(4, 2))
        self._active_rows = ctk.CTkFrame(self._active_frame, fg_color="transparent")
        self._active_rows.pack(fill="x")

        heading("Completed", 2)
        self._done_frame = ctk.CTkScrollableFrame(right, corner_radius=4, height=90)
        self._done_frame.grid(row=3, column=0, sticky="nsew", padx=10, pady=(0, 4))
        self._done_frame.grid_columnconfigure(0, weight=1)

        self._bam_label = heading("BAM files — select a sample", 4, pady=(6, 2))
        self._bam_scroll = ctk.CTkScrollableFrame(right, corner_radius=4, height=70)
        self._bam_scroll.grid(row=5, column=0, sticky="nsew", padx=10, pady=(0, 4))
        for box in (self._active_frame, self._done_frame, self._bam_scroll):
            self._compact_scrollframe(box)

        # Metrics
        mf = ctk.CTkFrame(right, corner_radius=6)
        mf.grid(row=6, column=0, sticky="ew", padx=10, pady=4)
        mf.grid_columnconfigure((0, 1, 2, 3), weight=1)
        self._m_tput  = self._metric(mf, "GB per hour",   "—", 0)
        self._m_files = self._metric(mf, "BAM files",     "—", 1)
        self._m_total = self._metric(mf, "Data written",  "—", 2)
        self._m_runs  = self._metric(mf, "Analyses done", "—", 3)

        # Output paths of the selected sample
        self._out_label_local = self._path_label(right, 7, "Sample output: —")
        self._out_label_lts   = self._path_label(right, 8, "Long-term storage: —")
        self._out_label_links = self._path_label(right, 9, "BAM links: —", pady=(0, 4))

        heading("Command being executed", 10)
        self._cmd_text = ctk.CTkTextbox(right, height=52, font=self.f_mono_s,
                                        state="disabled", corner_radius=4)
        self._cmd_text.grid(row=11, column=0, sticky="ew", padx=10, pady=(0, 4))

        heading("Log", 12)
        self._log_text = ctk.CTkTextbox(right, height=110, font=self.f_mono,
                                        state="disabled", corner_radius=6)
        self._log_text.grid(row=13, column=0, sticky="nsew", padx=10, pady=(0, 10))

    def _path_label(self, parent, row, text, pady=(0, 0)):
        lbl = ctk.CTkLabel(parent, text=text, height=18, font=self.f_mono_s,
                           text_color=MUTED, anchor="w")
        lbl.grid(row=row, column=0, padx=14, pady=pady, sticky="ew")
        return lbl

    def _metric(self, parent, label, value, col):
        card = ctk.CTkFrame(parent, corner_radius=6)
        card.grid(row=0, column=col, padx=6, pady=8, sticky="ew")
        ctk.CTkLabel(card, text=label, font=self.f_tiny, text_color=MUTED).pack(pady=(6, 0))
        lbl = ctk.CTkLabel(card, text=value, font=self.f_metric)
        lbl.pack(pady=(2, 6))
        return lbl

    # ── Settings tab ──────────────────────────────────────────────

    def _build_config_tab(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(0, weight=1)

        scroll = ctk.CTkScrollableFrame(parent, corner_radius=0)
        scroll.grid(row=0, column=0, sticky="nsew", padx=16, pady=(4, 0))
        scroll.grid_columnconfigure(1, weight=1)
        self._settings_scroll = scroll

        self._cfg_vars = {}

        def section(text, r, note=""):
            ctk.CTkLabel(scroll, text=text, font=self.f_bold, text_color=MUTED
                         ).grid(row=r, column=0, columnspan=2, padx=4, pady=(16, 2), sticky="w")
            if note:
                ctk.CTkLabel(scroll, text=note, font=self.f_tiny, text_color=MUTED, anchor="w",
                             justify="left", wraplength=640
                             ).grid(row=r + 1, column=0, columnspan=2, padx=4, pady=(0, 4),
                                    sticky="w")

        def hint_label(text, r):
            ctk.CTkLabel(scroll, text=text, font=self.f_tiny, text_color=MUTED,
                         anchor="w", wraplength=560, justify="left"
                         ).grid(row=r, column=1, sticky="w", padx=4, pady=(0, 6))

        def field(label, key, r, browse=False, is_dir=False, choices=None):
            ctk.CTkLabel(scroll, text=label, font=self.f_body, anchor="w", width=190
                         ).grid(row=r, column=0, padx=(4, 8), pady=(6, 0), sticky="nw")
            var = tk.StringVar(value=str(self._cfg.get(key, "")))
            self._cfg_vars[key] = var
            f = ctk.CTkFrame(scroll, fg_color="transparent")
            f.grid(row=r, column=1, sticky="ew", pady=(6, 0))
            f.grid_columnconfigure(0, weight=1)
            if choices:
                if var.get() not in choices:
                    var.set(choices[0])
                ctk.CTkOptionMenu(f, variable=var, values=choices, width=160, font=self.f_body
                                  ).grid(row=0, column=0, sticky="w")
            else:
                ctk.CTkEntry(f, textvariable=var, font=self.f_mono).grid(row=0, column=0,
                                                                         sticky="ew")
            if browse:
                ctk.CTkButton(f, text="Browse", width=64, font=self.f_small,
                              command=lambda k=key, v=var, d=is_dir: self._browse_cfg_path(k, v, d)
                              ).grid(row=0, column=1, padx=(4, 0))
            desc = cfg_module.DESCRIPTIONS.get(key, "")
            if desc:
                hint_label(desc, r + 1)

        r = 0
        section("SPARSH installation", r,
                "The SPARSH repository and the files its commands need. Leave the three file "
                "boxes blank when the files sit inside the SPARSH folder under their usual names; "
                "Check setup shows what was found."); r += 2
        field("SPARSH folder",    "sparsh_location", r, browse=True, is_dir=True); r += 2
        field("Reference FASTA",  "reference_fasta", r, browse=True);              r += 2
        field("EPIC manifest",    "epic_manifest",   r, browse=True);              r += 2
        field("Model folder",     "model_dir",       r, browse=True, is_dir=True); r += 2

        section("Conda environment", r); r += 2
        field("Environment name", "conda_env", r);              r += 2
        field("conda.sh",         "conda_sh",  r, browse=True); r += 2

        section("Analysis options", r); r += 2
        field("Copy-number panel file",      "panel",         r, browse=True); r += 2
        field("Blast percentage (default)",  "blast_percent", r);              r += 2
        field("Aligner",                     "aligner",       r, choices=["dorado", "minimap2"]); r += 2
        field("Threads",                     "threads",       r);              r += 2
        field("Prediction device",           "device",        r, choices=["cuda", "cpu"]); r += 2
        field("Extra run_sample.py options", "extra_args",    r);              r += 2

        section("Watching the run", r); r += 2
        field("Scan interval (seconds)",     "watch_interval",  r); r += 2
        field("BAM stable wait (seconds)",   "bam_stable_wait", r); r += 2

        section("Window", r); r += 2
        field("Appearance", "appearance", r, choices=["System", "Light", "Dark"]); r += 2

        footer = ctk.CTkFrame(parent, fg_color="transparent")
        footer.grid(row=1, column=0, pady=(6, 10), sticky="ew", padx=16)
        footer.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(footer, text=f"Settings file: {cfg_module.config_path()}",
                     font=self.f_tiny, text_color=MUTED, anchor="w"
                     ).grid(row=0, column=0, sticky="w")
        btns = ctk.CTkFrame(footer, fg_color="transparent")
        btns.grid(row=0, column=1, sticky="e")
        ctk.CTkButton(btns, text="Check setup", fg_color="transparent", border_width=1,
                      text_color=("gray10", "gray90"), command=self._check_setup,
                      font=self.f_body).pack(side="left", padx=4)
        ctk.CTkButton(btns, text="Reset to defaults", fg_color="transparent", border_width=1,
                      text_color=("gray10", "gray90"), command=self._reset_config,
                      font=self.f_body).pack(side="left", padx=4)
        ctk.CTkButton(btns, text="Save settings", command=self._save_config,
                      font=self.f_body).pack(side="left", padx=4)

    # ── Status bar ────────────────────────────────────────────────

    def _build_statusbar(self):
        bar = ctk.CTkFrame(self, height=26, corner_radius=0)
        bar.grid(row=2, column=0, sticky="ew")
        bar.grid_columnconfigure(3, weight=1)

        self._status_watch = ctk.CTkLabel(bar, text="Not watching", font=self.f_small,
                                          text_color=MUTED)
        self._status_watch.grid(row=0, column=0, padx=14, pady=4, sticky="w")
        self._status_bam_pass = ctk.CTkLabel(bar, text="", font=self.f_mono_s, text_color=MUTED)
        self._status_bam_pass.grid(row=0, column=1, padx=8, pady=4, sticky="w")
        self._status_counts = ctk.CTkLabel(bar, text="", font=self.f_small)
        self._status_counts.grid(row=0, column=2, padx=14, pady=4)
        self._status_scan = ctk.CTkLabel(bar, text="", font=self.f_small, text_color=MUTED)
        self._status_scan.grid(row=0, column=4, padx=14, pady=4, sticky="e")

    # ═════════════════════════════════════════════════════════════
    # REFRESH  (Tk main thread, every 2 s — background threads never touch widgets)
    # ═════════════════════════════════════════════════════════════

    def _start_refresh_loop(self):
        try:
            self._refresh_gui()
        except Exception as exc:              # a display glitch must not stop the loop
            print(f"[gui] refresh error: {exc}")
        self.after(2000, self._start_refresh_loop)

    @staticmethod
    def _set_label(widget, text: str, color=None):
        if widget.cget("text") != text:
            widget.configure(text=text)
        if color is not None and widget.cget("text_color") != color:
            widget.configure(text_color=color)

    def _set_textbox(self, box, text: str, follow: bool = True):
        """Replace a read-only textbox's text without losing the user's scroll position."""
        key = str(box)
        if self._tb_cache.get(key) == text:
            return
        self._tb_cache[key] = text
        first, last = box.yview()
        box.configure(state="normal")
        box.delete("1.0", "end")
        box.insert("end", text)
        box.configure(state="disabled")
        if follow and last >= 0.999:
            box.see("end")
        else:
            box.yview_moveto(first)

    def _refresh_gui(self):
        samples = app_state.get_all_samples()
        for s in samples:
            if s.name not in self._sample_rows:
                self._add_sample_row(s.name)
        for s in samples:
            row = self._sample_rows.get(s.name)
            if not row:
                continue
            tput = s.throughput_per_hour_gb()
            self._set_label(row["tput"], f"{tput:.1f}" if tput is not None else "—")
            self._set_label(row["status"], STATUS_LABELS.get(s.status, "?"),
                            STATUS_COLORS.get(s.status, GREY))
            done = str(s.analysis_count) + (f"/{s.max_repeats}" if s.max_repeats > 0 else "")
            self._set_label(row["runs"], done)

        activity = app_state.get_activity()
        self._refresh_active_panel(samples, activity)
        self._refresh_done_panel()
        if self._selected_sample:
            self._refresh_right(self._selected_sample, activity)
        self._refresh_command()
        self._refresh_statusbar(samples)

    _ROW_STYLE = {
        "running": (GREEN, "Running"),
        "queued":  (AMBER, "Queued"),
        "ok":      (BLUE,  "Done ✓"),
        "failed":  (RED,   "Failed ✗"),
        "ready":   (GREEN, "Ready"),
        "short":   (AMBER, "Not ready"),
    }

    def _refresh_active_panel(self, samples, act: Optional[dict]):
        info = app_state.get_run_info()
        rows = []                                # (kind, sample, label or None)
        if not self._running:
            paused = runner.paused_iteration()
            header = (f"Iteration {paused} is paused — press Start Analysis to resume."
                      if paused else "Not running — press Start Analysis.")
        elif not info.get("bam_pass"):
            header = "Looking for the bam_pass folder inside the run folder…"
        elif act and act.get("iteration") is not None:
            header = f"Iteration {act['iteration']} — {act.get('step') or ''}"
            if act.get("running"):
                header += f" on {act['running']}"
            if act.get("since"):
                header += f"  ·  {_elapsed(time.time() - act['since'])}"
            rows = ([("running", act["running"], None)] if act.get("running") else []) \
                + [("queued", n, None) for n in act.get("pending", [])] \
                + [("ok", n, None) for n in act.get("ok", [])] \
                + [("failed", n, None) for n in act.get("failed", [])]
        else:
            wait = (act or {}).get("wait")
            if not wait:
                header = "Waiting for BAM files."
            elif not wait["ready"] and not wait["short"]:
                header = "No samples left to analyse (all reached Max runs or were removed)."
            elif wait["finished"]:
                header = "Run complete — every BAM set has been analysed."
            else:
                k = wait["set"]
                header = (f"Waiting for BAM set {k}: file {k} (with its .bai) from every "
                          "barcode, since predict.py and report.py need all of them.")
                rows = [("ready", n, f"Set {k} ready") for n in wait["ready"]] \
                    + [("short", n, f"{have} of {k} files ready") for n, have in wait["short"]]

        self._set_label(self._active_header, header,
                        ("gray10", "gray90") if rows else MUTED)

        barcodes = {s.name: s.barcode_dir for s in samples}
        sig = (tuple(rows), tuple(sorted(barcodes.items())))
        if sig == self._sig_active:
            return
        self._sig_active = sig
        for w in self._active_rows.winfo_children():
            w.destroy()
        for kind, name, label in rows:
            color, default_label = self._ROW_STYLE[kind]
            fr = ctk.CTkFrame(self._active_rows, corner_radius=4, fg_color=ROW)
            fr.pack(fill="x", pady=2, padx=2)
            fr.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(fr, text="●", text_color=color, font=self.f_body
                         ).grid(row=0, column=0, padx=(8, 4), pady=2)
            ctk.CTkLabel(fr, text=f"{name}  /  {barcodes.get(name, '—')}", anchor="w",
                         font=self.f_bold).grid(row=0, column=1, sticky="w", pady=2)
            ctk.CTkLabel(fr, text=label or default_label, font=self.f_small, text_color=color
                         ).grid(row=0, column=2, padx=(0, 8), pady=2)

    def _refresh_done_panel(self):
        rounds = app_state.get_round_summaries()
        sig = tuple((r["number"], r["finished"]) for r in rounds)
        if sig == self._sig_done:
            return
        self._sig_done = sig
        for w in self._done_frame.winfo_children():
            w.destroy()
        if not rounds:
            ctk.CTkLabel(self._done_frame, text="No completed iterations yet.",
                         font=self.f_small, text_color=MUTED).pack(anchor="w", padx=8, pady=6)
            return
        for r in reversed(rounds):
            if r["predict_ok"] is False:
                result = "predict.py failed"
            elif r["report_ok"] is False:
                result = "report.py failed"
            elif r["report_ok"]:
                result = "report ready"
            else:
                result = "no report"
            if r["lts_ok"] is False:
                result += " · long-term storage copy failed"
            if r["report_ok"] and not r["failed"] and r["lts_ok"] is not False:
                color = GREEN
            elif r["report_ok"]:
                color = AMBER
            else:
                color = RED
            when = time.strftime("%H:%M", time.localtime(r["finished"]))
            samples_txt = "   ".join([f"{n} ✓" for n in r["ok"]] + [f"{n} ✗" for n in r["failed"]])

            fr = ctk.CTkFrame(self._done_frame, corner_radius=4, fg_color=ROW)
            fr.pack(fill="x", pady=2, padx=2)
            fr.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(fr, text="●", text_color=color, font=self.f_body
                         ).grid(row=0, column=0, rowspan=3, padx=(8, 4))
            ctk.CTkLabel(fr, text=f"Iteration {r['number']}  ·  {when}  ·  {result}", height=20,
                         font=self.f_bold, anchor="w").grid(row=0, column=1, sticky="w",
                                                            pady=(2, 0))
            ctk.CTkLabel(fr, text=samples_txt, font=self.f_small, height=18, text_color=MUTED,
                         anchor="w").grid(row=1, column=1, sticky="w")
            ctk.CTkLabel(fr, text=f"Folder: {r['iter_dir']}", height=18, font=self.f_mono_s,
                         text_color=MUTED, anchor="w", wraplength=380, justify="left"
                         ).grid(row=2, column=1, sticky="w", pady=(0, 2))
            ctk.CTkButton(fr, text="Open", width=52, height=22, font=self.f_small,
                          command=lambda p=r["iter_dir"]: self._open_folder(p)
                          ).grid(row=0, column=2, rowspan=3, padx=8)

    def _refresh_right(self, name: str, act: Optional[dict]):
        s = app_state.get_sample(name)
        if s is None:
            return

        # BAM list in set order — rebuilt only when files or their states change
        sig = tuple((b.name, b.size_bytes, b.ready, b.note, i <= s.files_analysed)
                    for i, b in enumerate(s.bam_files, start=1))
        if sig != self._sig_bams:
            self._sig_bams = sig
            for w in self._bam_scroll.winfo_children():
                w.destroy()
            if not s.bam_files:
                ctk.CTkLabel(self._bam_scroll, text="No BAM files found yet in "
                             f"{s.barcode_dir}.", font=self.f_small, text_color=MUTED
                             ).pack(anchor="w", padx=8, pady=12)
            for i, bf in enumerate(s.bam_files, start=1):
                if bf.ready and i <= s.files_analysed:
                    color, tag = BLUE, "analysed"
                elif bf.ready:
                    color, tag = GREEN, "ready"
                else:
                    color, tag = AMBER, bf.note or "not ready"
                fr = ctk.CTkFrame(self._bam_scroll, corner_radius=4, fg_color="transparent")
                fr.pack(fill="x", pady=1)
                ctk.CTkLabel(fr, text="●", text_color=color, font=self.f_tiny
                             ).pack(side="left", padx=(4, 6))
                ctk.CTkLabel(fr, text=tag, font=self.f_tiny, text_color=color, width=78
                             ).pack(side="right", padx=(0, 6))
                ctk.CTkLabel(fr, text=self._fmt_bytes(bf.size_bytes), font=self.f_small,
                             text_color=MUTED, width=60).pack(side="right")
                shown = bf.name if len(bf.name) <= 38 else bf.name[:24] + "…" + bf.name[-13:]
                ctk.CTkLabel(fr, text=f"{i:>2}  {shown}", font=self.f_mono, anchor="w"
                             ).pack(side="left", fill="x", expand=True)

        # Metrics
        tput = s.throughput_per_hour_gb()
        self._set_label(self._m_tput, f"{tput:.1f}" if tput is not None else "—")
        self._set_label(self._m_files, str(len(s.bam_files)))
        self._set_label(self._m_total, f"{s.total_bam_size_gb():.1f} GB")
        self._set_label(self._m_runs, str(s.analysis_count)
                        + (f" / {s.max_repeats}" if s.max_repeats > 0 else ""))

        # Folders: the iteration this sample is in now, else its last one
        k = s.last_round
        if act and act.get("iteration") is not None and name in (
                [act.get("running")] + act.get("pending", []) + act.get("ok", [])
                + act.get("failed", [])):
            k = act["iteration"]
        cfg = self._run_cfg or self._build_run_cfg()
        out_name = output_name(cfg)
        local, lts = cfg.get("local_output", ""), cfg.get("lts_output", "")
        iter_dir = Path(local) / out_name / str(k) if k and local and out_name else None
        local_txt = str(iter_dir / name) if iter_dir else "—"
        links_txt = str(iter_dir / "bam_links" / s.barcode_dir) if iter_dir else "—"
        if not lts:
            lts_txt = "not set"
        else:
            lts_txt = str(Path(lts) / out_name / str(k) / name) if k and out_name else "—"
        self._set_label(self._out_label_local, f"Sample output: {local_txt}")
        self._set_label(self._out_label_lts, f"Long-term storage: {lts_txt}")
        self._set_label(self._out_label_links, f"BAM links: {links_txt}")

        # Log (keeps the user's scroll position unless they are at the bottom)
        self._set_textbox(self._log_text, "\n".join(s.log_lines[-300:]))

    def _refresh_command(self):
        current, last = app_state.get_commands()
        if current:
            text = current
        elif last:
            text = "Nothing running right now. Last command:\n\n" + last
        else:
            text = "No command has run yet."
        self._set_textbox(self._cmd_text, text, follow=False)

    def _refresh_statusbar(self, samples):
        info = app_state.get_run_info()
        if self._running:
            if info.get("bam_pass"):
                self._set_label(self._status_watch, "Watching", GREEN)
                self._set_label(self._status_bam_pass, f"bam_pass: {info['bam_pass']}")
            else:
                self._set_label(self._status_watch, "Looking for bam_pass…", AMBER)
                self._set_label(self._status_bam_pass, f"in {info.get('run_folder', '')}")
        analysing = sum(1 for s in samples if s.status == SampleStatus.ANALYSING)
        queued = sum(1 for s in samples if s.status == SampleStatus.QUEUED)
        errors = sum(1 for s in samples if s.status == SampleStatus.ERROR)
        text = f"{len(samples)} samples  |  {analysing} analysing  |  {queued} queued"
        if errors:
            text += f"  |  {errors} with errors"
        self._set_label(self._status_counts, text)
        ts = app_state.get_last_scan()
        self._set_label(self._status_scan,
                        f"Last scan: {time.strftime('%H:%M:%S', time.localtime(ts))}" if ts else "")

    # ═════════════════════════════════════════════════════════════
    # SAMPLE TABLE
    # ═════════════════════════════════════════════════════════════

    def _add_sample_row(self, sample_name: str):
        s = app_state.get_sample(sample_name)
        if not s:
            return
        if self._empty_hint.winfo_manager():
            self._empty_hint.pack_forget()
        frame = ctk.CTkFrame(self._sample_scroll, corner_radius=4, fg_color="transparent")
        frame.pack(fill="x", pady=1)

        def select(_event=None, name=sample_name):
            self._select_sample(name)

        def hover(entering: bool, name=sample_name):
            if self._selected_sample != name:
                frame.configure(fg_color=ROW_HOVER if entering else "transparent")

        frame.bind("<Button-1>", select)
        frame.bind("<Enter>", lambda _e: hover(True))
        frame.bind("<Leave>", lambda _e: hover(False))

        def lbl(text, width, bold=False, mono=False, color=None):
            font = self.f_mono if mono else (self.f_bold if bold else self.f_small)
            kw = {"text_color": color} if color else {}
            l = ctk.CTkLabel(frame, text=text, font=font, width=width, anchor="w", **kw)
            l.pack(side="left", padx=1)
            l.bind("<Button-1>", select)
            return l

        shown = s.name if len(s.name) <= 18 else s.name[:17] + "…"
        lbl(shown, COLUMNS[0][1], bold=True)
        lbl(s.barcode_dir, COLUMNS[1][1], mono=True)
        lbl(s.sex or "—", COLUMNS[2][1])
        lbl(f"{s.blast_percent}%" if s.blast_percent else "—", COLUMNS[3][1], color=MUTED)
        lbl(str(s.max_repeats) if s.max_repeats > 0 else "no limit", COLUMNS[4][1], color=MUTED)
        runs_lbl = lbl("0", COLUMNS[5][1])
        tput_lbl = lbl("—", COLUMNS[6][1])
        status_lbl = lbl("Waiting", COLUMNS[7][1], color=GREY)

        ctk.CTkButton(frame, text="✕", width=26, height=22, fg_color="transparent",
                      text_color=MUTED, hover_color=("gray80", "gray30"), font=self.f_tiny,
                      command=lambda n=sample_name: self._remove_sample(n)
                      ).pack(side="right", padx=4)

        self._sample_rows[sample_name] = {"frame": frame, "tput": tput_lbl,
                                          "status": status_lbl, "runs": runs_lbl}

    def _select_sample(self, name: str):
        for r in self._sample_rows.values():
            r["frame"].configure(fg_color="transparent")
        row = self._sample_rows.get(name)
        if row:
            row["frame"].configure(fg_color=ROW_SEL)
        self._selected_sample = name
        self._sig_bams = None
        self._tb_cache.pop(str(self._log_text), None)
        s = app_state.get_sample(name)
        self._bam_label.configure(text=f"BAM files  —  {name}  /  {s.barcode_dir if s else ''}")
        self._refresh_right(name, app_state.get_activity())
        self._log_text.see("end")

    def _clear_right_panel(self):
        self._selected_sample = None
        self._sig_bams = None
        self._bam_label.configure(text="BAM files — select a sample")
        for w in self._bam_scroll.winfo_children():
            w.destroy()
        for widget in (self._m_tput, self._m_files, self._m_total, self._m_runs):
            self._set_label(widget, "—")
        self._set_label(self._out_label_local, "Sample output: —")
        self._set_label(self._out_label_lts, "Long-term storage: —")
        self._set_label(self._out_label_links, "BAM links: —")
        self._set_textbox(self._log_text, "")

    # ═════════════════════════════════════════════════════════════
    # ACTIONS
    # ═════════════════════════════════════════════════════════════

    def _setup_problems(self) -> list:
        problems = []
        run_folder = self._run_folder_var.get().strip()
        if not run_folder or not Path(run_folder).expanduser().is_dir():
            problems.append("Run folder: choose an existing folder.")
        if not self._local_out_var.get().strip():
            problems.append("Local output: choose a folder.")
        name = self._out_name_var.get().strip()
        if not name:
            problems.append("Output folder name: enter a name (default is today's date, dd_mm_yyyy).")
        elif not OUTPUT_NAME_RE.fullmatch(name) or name in (".", ".."):
            problems.append("Output folder name can only use letters, numbers, '.', '_' and '-' "
                            "(no spaces or slashes).")
        if not app_state.get_all_samples():
            problems.append("Add at least one sample.")
        problems += sparsh_paths.resolve(self._cfg).problems
        conda_sh = str(self._cfg.get("conda_sh", "")).strip()
        if not conda_sh or not Path(conda_sh).expanduser().is_file():
            problems.append(f"conda.sh not found: {conda_sh or '(blank)'} (Settings).")
        return problems

    def _environment_warning(self) -> Optional[str]:
        """A warning when the conda environment is not where environments usually are."""
        env = str(self._cfg.get("conda_env", "")).strip()
        dirs = sparsh_paths.conda_env_dirs(str(self._cfg.get("conda_sh", "")), env)
        if not env:
            return "The conda environment name is blank (Settings)."
        if dirs and not any(d.is_dir() for d in dirs):
            looked = "\n".join(f"  {d}" for d in dirs)
            return (f"No conda environment named '{env}' was found in\n{looked}\n\n"
                    "Every command activates it, so it will fail if the name is wrong.")
        return None

    def _start_analysis(self):
        if self._running:
            return
        problems = self._setup_problems()
        if problems:
            messagebox.showwarning("Can't start yet", "\n\n".join(problems))
            return
        warning = self._environment_warning()
        if warning and not messagebox.askyesno("Conda environment", warning + "\n\nStart anyway?"):
            return
        cfg = self._build_run_cfg()
        lts = cfg["lts_output"]
        if lts and not Path(lts).expanduser().is_dir():
            if not messagebox.askyesno(
                    "Long-term storage",
                    f"The long-term storage folder does not exist:\n{lts}\n\n"
                    "If it is a network or external drive, check that it is mounted. "
                    "Create the folder and continue?"):
                return
        if not runner.can_resume(cfg) and not self._confirm_output_folder(cfg):
            return
        for key in ("local_output", "lts_output"):
            if cfg[key]:
                try:
                    Path(cfg[key]).expanduser().mkdir(parents=True, exist_ok=True)
                except OSError as exc:
                    messagebox.showerror("Output folder", f"Cannot create {cfg[key]}:\n{exc}")
                    return

        self._cfg["last_run_folder"] = cfg["run_folder"]
        self._cfg["last_local_output"] = cfg["local_output"]
        self._cfg["last_lts_output"] = cfg["lts_output"]
        try:
            cfg_module.save(self._cfg)
        except OSError as exc:                 # the folders are then not remembered; start anyway
            print(f"[gui] could not write {cfg_module.config_path()}: {exc}")

        self._run_cfg = cfg
        self._started_outputs.add(self._output_key(cfg))
        app_state.set_run_folder(str(Path(cfg["run_folder"]).expanduser()), cfg["local_output"])
        runner.start(cfg)
        self._watcher = BamWatcher(watch_interval=self._cfg_int("watch_interval", 30),
                                   bam_stable_wait=self._cfg_int("bam_stable_wait", 60),
                                   on_scan_complete=runner.poll)
        self._watcher.start()
        self._running = True
        self._start_btn.configure(state="disabled", text="Running…", fg_color=BLUE)
        self._stop_btn.configure(state="normal", fg_color=RED, hover_color=RED_DARK)
        self._sig_active = None
        try:                                   # the sample list, for Import after a restart
            self._write_sample_list(output_roots(cfg)[0] / SAMPLE_LIST_FILE)
        except OSError as exc:
            print(f"[gui] could not write {SAMPLE_LIST_FILE}: {exc}")

    def _stop_analysis(self):
        if not self._running:
            return
        if self._watcher:
            self._watcher.stop()
            self._watcher = None
        runner.stop()
        self._running = False
        self._start_btn.configure(state="normal", text="Start Analysis",
                                  fg_color=GREEN, hover_color=GREEN_DARK)
        self._stop_btn.configure(state="disabled", fg_color=GREY, hover_color=RED)
        self._set_label(self._status_watch, "Stopped", RED)
        self._set_label(self._status_bam_pass, "")
        self._sig_active = None

    def _browse_folder(self, var: tk.StringVar, title: str):
        current = var.get().strip()
        d = filedialog.askdirectory(title=title,
                                    initialdir=current if current and Path(current).is_dir() else None)
        if d:
            var.set(d)

    def _browse_cfg_path(self, key: str, var: tk.StringVar, is_dir: bool = False):
        label = key.replace("_", " ")
        current = var.get().strip()
        start = Path(current).expanduser() if current else None
        if is_dir:
            d = filedialog.askdirectory(title=f"Select the {label}",
                                        initialdir=start if start and start.is_dir() else None)
            if d:
                var.set(d)
        else:
            f = filedialog.askopenfilename(
                title=f"Select the {label}",
                initialdir=start.parent if start and start.parent.is_dir() else None)
            if f:
                var.set(f)

    def _add_sample(self):
        error, values = validate_sample(self._new_sample_var.get(), self._new_barcode_var.get(),
                                        self._new_sex_var.get(), self._new_blast_var.get(),
                                        self._new_repeats_var.get())
        if error:
            self._add_err_var.set(error)
            return
        error = self._add_validated(values)
        if error:
            self._add_err_var.set(error)
            return
        self._add_err_var.set("")
        self._new_sample_var.set("")
        self._new_barcode_var.set("")
        self._new_sex_var.set(SEX_NONE)
        self._new_blast_var.set("")
        self._new_repeats_var.set("0")
        self._new_sample_entry.focus_set()

    def _add_validated(self, values: tuple) -> Optional[str]:
        """Add a sample whose fields passed validate_sample. Returns an error or None."""
        name, barcode, sex, blast, max_rep = values
        owner = app_state.barcode_owner(barcode)
        if owner:
            return f"barcode{barcode} is already used by {owner}."
        existing = app_state.sample_named(name)
        if existing is not None:
            return f"Sample '{existing}' already exists."
        if not app_state.add_sample(name, barcode, sex, blast, max_rep):
            return f"Sample '{name}' could not be added."
        self._add_sample_row(name)
        return None

    def _remove_sample(self, name: str):
        s = app_state.get_sample(name)
        if s and s.status == SampleStatus.ANALYSING:
            messagebox.showinfo("Sample is being analysed",
                                f"{name} is being analysed right now.\n"
                                "Wait until it finishes, or press Stop Analysis first.")
            return
        if not messagebox.askyesno("Remove sample", f"Remove sample '{name}'?"):
            return
        runner.forget(name)
        row = self._sample_rows.pop(name, None)
        if row:
            row["frame"].destroy()
        app_state.remove_sample(name)
        if self._selected_sample == name:
            self._clear_right_panel()
        if not self._sample_rows:
            self._empty_hint.pack(anchor="w", padx=6, pady=12)

    # ── sample lists (CSV or TSV: sample, barcode, sex, blast_percent, max_runs) ──

    def _import_samples(self):
        path = filedialog.askopenfilename(
            title="Import a sample list",
            filetypes=[("CSV or TSV", "*.csv *.tsv *.txt"), ("All files", "*")])
        if not path:
            return
        try:
            rows, errors = self._read_sample_list(Path(path))
        except (OSError, csv.Error, ValueError) as exc:
            messagebox.showerror("Import failed", f"{path}\n\n{exc}")
            return
        added = 0
        for line_no, fields in rows:
            error, values = validate_sample(*fields)
            if error is None:
                error = self._add_validated(values)
            if error:
                errors.append(f"line {line_no}: {error}")
            else:
                added += 1
        text = f"{added} sample(s) added from {Path(path).name}."
        if errors:
            shown = errors[:12] + ([f"… and {len(errors) - 12} more"] if len(errors) > 12 else [])
            text += "\n\nNot added:\n" + "\n".join(shown)
            messagebox.showwarning("Import", text)
        else:
            messagebox.showinfo("Import", text)

    @staticmethod
    def _read_sample_list(path: Path) -> Tuple[List[tuple], List[str]]:
        """Rows as (line number, (sample, barcode, sex, blast, max_runs)) plus errors."""
        text = path.read_text(encoding="utf-8-sig")
        first = next((line for line in text.splitlines() if line.strip()), "")
        delimiter = "\t" if (path.suffix.lower() == ".tsv" or "\t" in first) else ","
        reader = csv.reader(text.splitlines(), delimiter=delimiter)
        header = None
        rows, errors = [], []
        aliases = {"sample": ("sample", "sample_id", "sampleid", "name", "id"),
                   "barcode": ("barcode", "bc", "barcode_number"),
                   "sex": ("sex", "gender"),
                   "blast": ("blast_percent", "blast", "blast_%", "blasts", "blast_percentage"),
                   "max_runs": ("max_runs", "max_repeats", "maxruns", "runs")}
        for line_no, cells in enumerate(reader, start=1):
            cells = [c.strip() for c in cells]
            if not any(cells) or cells[0].startswith("#"):
                continue
            if header is None:
                lowered = [c.lower().replace(" ", "_") for c in cells]
                header = {}
                for key, names in aliases.items():
                    for i, col in enumerate(lowered):
                        if col in names:
                            header[key] = i
                            break
                if "sample" not in header or "barcode" not in header:
                    raise ValueError("The first row must name the columns, with at least "
                                     "'sample' and 'barcode' (optional: sex, blast_percent, "
                                     f"max_runs). Found: {', '.join(cells)}")
                continue

            def cell(key):
                i = header.get(key)
                return cells[i] if i is not None and i < len(cells) else ""

            rows.append((line_no, (cell("sample"), cell("barcode"), cell("sex"),
                                   cell("blast"), cell("max_runs") or "0")))
        return rows, errors

    def _export_samples(self):
        if not app_state.get_all_samples():
            messagebox.showinfo("Export", "There are no samples to export.")
            return
        path = filedialog.asksaveasfilename(
            title="Save the sample list", defaultextension=".csv",
            initialfile=SAMPLE_LIST_FILE, filetypes=[("CSV", "*.csv")])
        if not path:
            return
        try:
            self._write_sample_list(Path(path))
        except OSError as exc:
            messagebox.showerror("Export failed", f"{path}\n\n{exc}")

    @staticmethod
    def _write_sample_list(path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["sample", "barcode", "sex", "blast_percent", "max_runs"])
            for s in app_state.get_all_samples():
                w.writerow([s.name, s.barcode, s.sex, s.blast_percent, s.max_repeats])

    # ── Settings ──────────────────────────────────────────────────

    def _settings_from_vars(self) -> Tuple[Optional[str], dict]:
        """The Settings tab's values, checked. Returns (error or None, values)."""
        values = {}
        for key, var in self._cfg_vars.items():
            val = var.get().strip()
            if key in ("watch_interval", "bam_stable_wait", "threads"):
                try:
                    val = int(val)
                    if val < 1:
                        raise ValueError
                except ValueError:
                    label = {"watch_interval": "Scan interval", "bam_stable_wait": "BAM stable wait",
                             "threads": "Threads"}[key]
                    return f"{label} must be a whole number above 0.", {}
            elif key == "blast_percent" and val:
                try:
                    number = float(val.rstrip("%"))
                except ValueError:
                    number = -1.0
                if not 0 < number <= 100:
                    return "Blast percentage must be above 0 and at most 100, or blank.", {}
                val = f"{number:g}"
            elif key == "extra_args" and val:
                try:
                    shlex.split(val)
                except ValueError as exc:
                    return f"Extra run_sample.py options could not be read: {exc}", {}
            values[key] = val
        return None, values

    def _save_config(self):
        error, values = self._settings_from_vars()
        if error:
            messagebox.showerror("Invalid value", error)
            return
        self._cfg.update(values)
        try:
            cfg_module.save(self._cfg)
        except OSError as exc:
            messagebox.showerror("Settings not saved",
                                 f"Could not write {cfg_module.config_path()}:\n{exc}")
            return
        ctk.set_appearance_mode(str(self._cfg.get("appearance") or "System"))
        notes = sparsh_paths.resolve(self._cfg).problems
        conda_sh = str(self._cfg.get("conda_sh", "")).strip()
        if not conda_sh or not Path(conda_sh).expanduser().is_file():
            notes.append(f"conda.sh not found: {conda_sh or '(blank)'}")
        if self._running:
            # Paths chosen at Start stay; global settings apply from the next command.
            self._run_cfg.update({k: v for k, v in self._cfg.items() if not k.startswith("last_")})
            runner.set_config(self._run_cfg)
            text = ("Settings saved. They apply from the next command; the scan interval and "
                    "BAM stable wait apply after Stop and Start.")
        else:
            text = "Settings saved."
        if notes:
            text += "\n\nStill to fix before an analysis can start:\n" + "\n".join(f"• {n}" for n in notes)
            messagebox.showwarning("Saved", text)
        else:
            messagebox.showinfo("Saved", text)

    def _check_setup(self):
        """Show which SPARSH files were found with the values on the Settings tab."""
        error, values = self._settings_from_vars()
        if error:
            messagebox.showerror("Invalid value", error)
            return
        cfg = dict(self._cfg)
        cfg.update(values)
        found = sparsh_paths.resolve(cfg)
        lines = [found.summary()] if found.summary() else []
        problems = list(found.problems)
        conda_sh = str(cfg.get("conda_sh", "")).strip()
        if conda_sh and Path(conda_sh).expanduser().is_file():
            lines.append(f"conda.sh: {conda_sh}")
        else:
            problems.append(f"conda.sh not found: {conda_sh or '(blank)'}")
        env = str(cfg.get("conda_env", "")).strip()
        env_dirs = sparsh_paths.conda_env_dirs(conda_sh, env)
        hit = next((d for d in env_dirs if d.is_dir()), None)
        if hit:
            lines.append(f"Environment '{env}': {hit}")
        elif env:
            problems.append(f"Environment '{env}' not found in the usual places "
                            "(it may still exist elsewhere).")
        else:
            problems.append("The conda environment name is blank.")
        text = "\n".join(lines)
        if problems:
            text += "\n\nProblems:\n" + "\n".join(f"• {p}" for p in problems)
            messagebox.showwarning("Check setup", text)
        else:
            messagebox.showinfo("Check setup", text + "\n\nEverything was found.")

    def _reset_config(self):
        if messagebox.askyesno("Reset", "Reset every setting on this tab to its default?"):
            for key, var in self._cfg_vars.items():
                var.set(str(cfg_module.DEFAULTS.get(key, "")))

    # ═════════════════════════════════════════════════════════════
    # HELPERS
    # ═════════════════════════════════════════════════════════════

    def _build_run_cfg(self) -> dict:
        cfg = dict(self._cfg)
        cfg["local_output"] = self._local_out_var.get().strip()
        cfg["lts_output"] = self._lts_out_var.get().strip()
        cfg["run_folder"] = self._run_folder_var.get().strip()
        cfg["output_name"] = self._out_name_var.get().strip()
        return cfg

    @staticmethod
    def _output_key(cfg: dict) -> tuple:
        return (cfg["run_folder"], cfg["local_output"], cfg["lts_output"], cfg["output_name"])

    def _update_output_hint(self):
        name = self._out_name_var.get().strip() or "<name>"
        local = self._local_out_var.get().strip() or "<Local output>"
        text = f"Results go to {Path(local) / name}/<iteration>/"
        lts = self._lts_out_var.get().strip()
        if lts:
            text += f"   and   {Path(lts) / name}/<iteration>/"
        self._out_hint.configure(text=text)

    def _open_output_root(self):
        local = self._local_out_var.get().strip()
        name = self._out_name_var.get().strip()
        if not local:
            messagebox.showinfo("Output folder", "Choose a local output folder first.")
            return
        root = Path(local).expanduser() / name if name else Path(local).expanduser()
        self._open_folder(str(root if root.exists() else Path(local).expanduser()))

    def _confirm_output_folder(self, cfg: dict) -> bool:
        """Refuse a folder that belongs to another run; ask before adding to existing iterations."""
        if self._output_key(cfg) in self._started_outputs:
            return True                               # already confirmed in this session
        name = cfg["output_name"]
        this_run = os.path.realpath(os.path.expanduser(cfg["run_folder"]))
        roots = output_roots(cfg)
        for root in roots:
            other = recorded_run(root)
            if other and os.path.realpath(other) != this_run:
                messagebox.showwarning(
                    "Output folder name already used",
                    f"'{name}' in {root.parent} already holds results from another run:\n"
                    f"{other}\n\nEnter a different output folder name for this run.")
                return False
        done = highest_iteration(roots)
        if done:
            whose = "from this run" if any(recorded_run(r) for r in roots) else "already"
            have = "iteration 1" if done == 1 else f"iterations 1–{done}"
            if not messagebox.askyesno(
                    "Continue in this folder?",
                    f"'{name}' contains {have} {whose}.\n\n"
                    f"New iterations will continue from {done + 1} (BAM set {done + 1}).\n\n"
                    "Continue? Choose No to enter a different output folder name."):
                return False
        return True

    @staticmethod
    def _compact_scrollframe(frame):
        """CTkScrollableFrame's scrollbar asks for 200 px, which overrides the frame's
        own height; let the frame decide (the scrollbar still fills it)."""
        try:
            frame._scrollbar.configure(height=10)
        except Exception:
            pass

    def _cfg_int(self, key: str, default: int) -> int:
        try:
            return max(1, int(self._cfg.get(key, default)))
        except (TypeError, ValueError):
            return default

    def _open_folder(self, path: str):
        if not Path(path).exists():
            messagebox.showinfo("Folder not found", path)
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)
            else:
                opener = "open" if sys.platform == "darwin" else "xdg-open"
                subprocess.Popen([opener, path], env=subprocess_env(),
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
        except Exception as exc:
            messagebox.showerror("Can't open folder", f"{path}\n\n{exc}")

    @staticmethod
    def _fmt_bytes(n: float) -> str:
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024:
                return f"{n:.0f} {unit}"
            n /= 1024
        return f"{n:.1f} TB"

    def on_closing(self):
        if runner.is_busy() and not messagebox.askyesno(
                "Quit", "An analysis is running. Stop it and quit?"):
            return
        if self._watcher:
            self._watcher.stop()
        runner.stop()
        if not runner.wait_for_jobs(timeout=3.0):   # a job that ignores SIGTERM is killed now,
            runner.kill_now()                       # since nothing would do it after the exit
            runner.wait_for_jobs(timeout=2.0)
        self.destroy()


def run():
    app = App()
    app.protocol("WM_DELETE_WINDOW", app.on_closing)
    app.mainloop()
