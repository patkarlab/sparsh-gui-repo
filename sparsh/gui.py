"""
gui.py — SPARSH ONT Run Manager main window.
"""
import os
import re
import subprocess
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Optional

import customtkinter as ctk

from . import config as cfg_module
from .runner import (SPARSH_FILES, highest_iteration, output_name, output_roots,
                     recorded_run, runner, subprocess_env)
from .state import SampleStatus, app_state, normalise_barcode
from .watcher import BamWatcher

ctk.set_appearance_mode("System")
ctk.set_default_color_theme("blue")

STATUS_COLORS = {
    SampleStatus.WAITING:    "#888888",
    SampleStatus.SEQUENCING: "#6C8EBF",
    SampleStatus.QUEUED:     "#E0A050",
    SampleStatus.ANALYSING:  "#4FA84F",
    SampleStatus.DONE:       "#378ADD",
    SampleStatus.ERROR:      "#E24B4A",
}
STATUS_LABELS = {
    SampleStatus.WAITING:    "Waiting",
    SampleStatus.SEQUENCING: "Sequencing",
    SampleStatus.QUEUED:     "Queued",
    SampleStatus.ANALYSING:  "Analysing",
    SampleStatus.DONE:       "Done",
    SampleStatus.ERROR:      "Error",
}
SAMPLE_ID_RE = re.compile(r"[A-Za-z0-9._-]+")
OUTPUT_NAME_RE = SAMPLE_ID_RE          # one folder name: no spaces or slashes


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("SPARSH - ONT Run Manager")
        width = min(1150, self.winfo_screenwidth() - 40)
        height = max(600, min(900, self.winfo_screenheight() - 80))
        self.geometry(f"{width}x{height}")
        self.minsize(960, 600)

        self._cfg = cfg_module.load()
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
        self._tabs.add("Active Run")
        self._tabs.add("Global Config")
        self._build_run_tab(self._tabs.tab("Active Run"))
        self._build_config_tab(self._tabs.tab("Global Config"))

        self._build_statusbar()

    # ── Top bar ───────────────────────────────────────────────────

    def _build_topbar(self):
        bar = ctk.CTkFrame(self, height=48, corner_radius=0)
        bar.grid(row=0, column=0, sticky="ew")
        bar.grid_columnconfigure(1, weight=2)   # run folder entry expands most
        bar.grid_columnconfigure(4, weight=1)   # local output entry
        bar.grid_columnconfigure(7, weight=1)   # lts output entry

        # Run folder
        ctk.CTkLabel(bar, text="Run folder", font=ctk.CTkFont(size=12)
                     ).grid(row=0, column=0, padx=(12, 4), pady=10, sticky="w")
        self._run_folder_var = tk.StringVar(value=self._cfg.get("last_run_folder", ""))
        ctk.CTkEntry(bar, textvariable=self._run_folder_var,
                     font=ctk.CTkFont(size=11, family="monospace")
                     ).grid(row=0, column=1, padx=(0, 2), pady=10, sticky="ew")
        ctk.CTkButton(bar, text="Browse", width=64, command=self._browse_run_folder,
                      font=ctk.CTkFont(size=12)
                      ).grid(row=0, column=2, padx=(0, 12), pady=10)

        # Local output
        ctk.CTkLabel(bar, text="Local output", font=ctk.CTkFont(size=12)
                     ).grid(row=0, column=3, padx=(0, 4), pady=10)
        self._local_out_var = tk.StringVar(value=self._cfg.get("last_local_output", ""))
        ctk.CTkEntry(bar, textvariable=self._local_out_var,
                     font=ctk.CTkFont(size=11, family="monospace")
                     ).grid(row=0, column=4, padx=(0, 2), pady=10, sticky="ew")
        ctk.CTkButton(bar, text="Browse", width=64,
                      command=lambda: self._browse_folder(self._local_out_var,
                                                          "Select local output folder"),
                      font=ctk.CTkFont(size=12)
                      ).grid(row=0, column=5, padx=(0, 12), pady=10)

        # LTS output
        ctk.CTkLabel(bar, text="LTS output", font=ctk.CTkFont(size=12)
                     ).grid(row=0, column=6, padx=(0, 4), pady=10)
        self._lts_out_var = tk.StringVar(value=self._cfg.get("last_lts_output", ""))
        ctk.CTkEntry(bar, textvariable=self._lts_out_var,
                     font=ctk.CTkFont(size=11, family="monospace")
                     ).grid(row=0, column=7, padx=(0, 2), pady=10, sticky="ew")
        ctk.CTkButton(bar, text="Browse", width=64,
                      command=lambda: self._browse_folder(self._lts_out_var,
                                                          "Select LTS folder (optional)"),
                      font=ctk.CTkFont(size=12)
                      ).grid(row=0, column=8, padx=(0, 12), pady=10)

        # Output folder name (default: today's date) — holds the iteration folders
        ctk.CTkLabel(bar, text="Output folder name", font=ctk.CTkFont(size=12)
                     ).grid(row=1, column=0, padx=(12, 4), pady=(0, 10), sticky="w")
        name_row = ctk.CTkFrame(bar, fg_color="transparent")
        name_row.grid(row=1, column=1, columnspan=8, padx=(0, 12), pady=(0, 10), sticky="w")
        self._out_name_var = tk.StringVar(value=time.strftime("%d_%m_%Y"))
        ctk.CTkEntry(name_row, textvariable=self._out_name_var, width=150,
                     font=ctk.CTkFont(size=11, family="monospace")).pack(side="left")
        self._out_hint = ctk.CTkLabel(name_row, text="", font=ctk.CTkFont(size=10),
                                      text_color="#888888", anchor="w")
        self._out_hint.pack(side="left", padx=(10, 0))
        for var in (self._out_name_var, self._local_out_var, self._lts_out_var):
            var.trace_add("write", lambda *_: self._update_output_hint())
        self._update_output_hint()

    # ── Active Run tab ────────────────────────────────────────────

    def _build_run_tab(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_columnconfigure(1, weight=1)
        parent.grid_rowconfigure(0, weight=1)

        # ══ LEFT PANEL ════════════════════════════════════════════
        left = ctk.CTkFrame(parent, corner_radius=8)
        left.grid(row=0, column=0, sticky="nsew", padx=(8, 4), pady=8)
        left.grid_rowconfigure(2, weight=1)
        left.grid_columnconfigure(0, weight=1)

        # Title + Start/Stop buttons
        title_row = ctk.CTkFrame(left, fg_color="transparent")
        title_row.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 4))
        title_row.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(title_row, text="Samples",
                     font=ctk.CTkFont(size=13, weight="bold")
                     ).grid(row=0, column=0, sticky="w")
        self._start_btn = ctk.CTkButton(
            title_row, text="Start Analysis", width=110,
            fg_color="#4FA84F", hover_color="#3A8A3A",
            command=self._start_analysis, font=ctk.CTkFont(size=12))
        self._start_btn.grid(row=0, column=1, padx=(6, 4))
        self._stop_btn = ctk.CTkButton(
            title_row, text="Stop Analysis", width=110,
            fg_color="#888888", hover_color="#E24B4A",
            command=self._stop_analysis, font=ctk.CTkFont(size=12))
        self._stop_btn.grid(row=0, column=2, padx=(0, 2))
        self._stop_btn.configure(state="disabled")

        # Column headers
        hdr = ctk.CTkFrame(left, fg_color="transparent")
        hdr.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 2))
        for col, w in [("Sample ID", 90), ("Barcode", 76), ("Sex", 36),
                       ("Blast %", 48), ("Max runs", 52), ("Done", 40),
                       ("GB/hr", 48), ("Status", 80)]:
            ctk.CTkLabel(hdr, text=col, font=ctk.CTkFont(size=10),
                         text_color="#888888", width=w, anchor="w"
                         ).pack(side="left", padx=1)

        self._sample_scroll = ctk.CTkScrollableFrame(left, corner_radius=0)
        self._sample_scroll.grid(row=2, column=0, sticky="nsew", padx=8, pady=2)

        # ── Add sample form ───────────────────────────────────────
        add_outer = ctk.CTkFrame(left, corner_radius=6)
        add_outer.grid(row=3, column=0, sticky="ew", padx=8, pady=(6, 10))
        add_outer.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(add_outer, text="Add sample",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color="#888888"
                     ).grid(row=0, column=0, sticky="w", padx=10, pady=(8, 2))

        # One small column per field: label on top, input underneath, both
        # pinned to the left edge so every label starts where its input starts.
        form = ctk.CTkFrame(add_outer, fg_color="transparent")
        form.grid(row=1, column=0, sticky="w", padx=10, pady=(4, 4))

        sm = ctk.CTkFont(size=10)
        tc = "#888888"

        self._new_sample_var  = tk.StringVar()
        self._new_barcode_var = tk.StringVar()
        self._new_sex_var     = tk.StringVar(value="")
        self._new_blast_var   = tk.StringVar(value="")
        self._new_repeats_var = tk.StringVar(value="0")
        self._add_err_var     = tk.StringVar()

        def field(column, label, make_input):
            box = ctk.CTkFrame(form, fg_color="transparent")
            box.grid(row=0, column=column, sticky="sw", padx=(0, 6))
            ctk.CTkLabel(box, text=label, font=sm, text_color=tc, anchor="w"
                         ).pack(anchor="w")
            widget = make_input(box)
            widget.pack(anchor="w", pady=(0, 4))
            return widget

        field(0, "Sample ID  *", lambda p: ctk.CTkEntry(
            p, textvariable=self._new_sample_var, width=104, font=ctk.CTkFont(size=12)))
        field(1, "Barcode  *  (01–99)", lambda p: ctk.CTkEntry(
            p, textvariable=self._new_barcode_var, width=56, font=ctk.CTkFont(size=12)))
        field(2, "Sex", lambda p: ctk.CTkOptionMenu(
            p, variable=self._new_sex_var, values=["", "F", "M"], width=64,
            font=ctk.CTkFont(size=12)))
        field(3, "Blast %  (0–100)", lambda p: ctk.CTkEntry(
            p, textvariable=self._new_blast_var, width=56, font=ctk.CTkFont(size=12)))
        field(4, "Max runs", lambda p: ctk.CTkEntry(
            p, textvariable=self._new_repeats_var, width=56, font=ctk.CTkFont(size=12)))
        ctk.CTkButton(form, text="+ Add", width=72, command=self._add_sample,
                      font=ctk.CTkFont(size=12)
                      ).grid(row=0, column=5, sticky="s", pady=(0, 4))

        hint_err = ctk.CTkFrame(add_outer, fg_color="transparent")
        hint_err.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 8))
        ctk.CTkLabel(hint_err,
                     text="* Required.  Sex, Blast % and Max runs are optional.  "
                          "Blast % overrides Global Config.  Max runs = 0 = unlimited.",
                     font=ctk.CTkFont(size=10), text_color="#888888", anchor="w"
                     ).pack(side="left")
        ctk.CTkLabel(add_outer, textvariable=self._add_err_var,
                     text_color="#E24B4A", font=ctk.CTkFont(size=11)
                     ).grid(row=4, column=0, sticky="w", padx=10, pady=(0, 4))

        # ══ RIGHT PANEL ═══════════════════════════════════════════
        right = ctk.CTkFrame(parent, corner_radius=8)
        right.grid(row=0, column=1, sticky="nsew", padx=(4, 8), pady=8)
        right.grid_columnconfigure(0, weight=1)
        for row, share in ((1, 2), (3, 2), (5, 2), (13, 3)):   # boxes share the height
            right.grid_rowconfigure(row, weight=share)

        # Currently analysing
        ctk.CTkLabel(right, text="Currently analysing",
                     font=ctk.CTkFont(size=12, weight="bold")
                     ).grid(row=0, column=0, padx=12, pady=(10, 2), sticky="w")
        self._active_frame = ctk.CTkScrollableFrame(right, corner_radius=4, height=72)
        self._active_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 4))
        self._active_frame.grid_columnconfigure(0, weight=1)

        # Completed
        ctk.CTkLabel(right, text="Completed",
                     font=ctk.CTkFont(size=12, weight="bold")
                     ).grid(row=2, column=0, padx=12, pady=(4, 2), sticky="w")
        self._done_frame = ctk.CTkScrollableFrame(right, corner_radius=4, height=84)
        self._done_frame.grid(row=3, column=0, sticky="nsew", padx=8, pady=(0, 4))
        self._done_frame.grid_columnconfigure(0, weight=1)

        # BAM files for selected sample
        self._bam_label = ctk.CTkLabel(right, text="BAM files — select a sample",
                                        font=ctk.CTkFont(size=12, weight="bold"))
        self._bam_label.grid(row=4, column=0, padx=12, pady=(6, 2), sticky="w")
        self._bam_scroll = ctk.CTkScrollableFrame(right, corner_radius=0, height=64)
        self._bam_scroll.grid(row=5, column=0, sticky="nsew", padx=8, pady=(0, 4))
        for box in (self._active_frame, self._done_frame, self._bam_scroll):
            self._compact_scrollframe(box)

        # Metrics
        mf = ctk.CTkFrame(right, corner_radius=6)
        mf.grid(row=6, column=0, sticky="ew", padx=8, pady=4)
        mf.grid_columnconfigure((0, 1, 2, 3), weight=1)
        self._m_tput  = self._metric(mf, "GB / hr",       "—", 0)
        self._m_files = self._metric(mf, "BAM files",     "—", 1)
        self._m_total = self._metric(mf, "Total written", "—", 2)
        self._m_runs  = self._metric(mf, "Runs done",     "—", 3)

        # Output paths
        self._out_label_local = ctk.CTkLabel(
            right, text="Local : —", height=18,
            font=ctk.CTkFont(size=10, family="monospace"),
            text_color="#888888", anchor="w")
        self._out_label_local.grid(row=7, column=0, padx=12, sticky="ew")
        self._out_label_lts = ctk.CTkLabel(
            right, text="LTS   : —", height=18,
            font=ctk.CTkFont(size=10, family="monospace"),
            text_color="#888888", anchor="w")
        self._out_label_lts.grid(row=8, column=0, padx=12, sticky="ew")
        self._out_label_links = ctk.CTkLabel(
            right, text="BAMs  : —", height=18,
            font=ctk.CTkFont(size=10, family="monospace"),
            text_color="#888888", anchor="w")
        self._out_label_links.grid(row=9, column=0, padx=12, pady=(0, 4), sticky="ew")

        # Command
        ctk.CTkLabel(right, text="Command being executed",
                     font=ctk.CTkFont(size=12, weight="bold")
                     ).grid(row=10, column=0, padx=12, pady=(4, 2), sticky="w")
        self._cmd_text = ctk.CTkTextbox(
            right, height=56,
            font=ctk.CTkFont(size=10, family="monospace"),
            state="disabled", corner_radius=4)
        self._cmd_text.grid(row=11, column=0, sticky="ew", padx=8, pady=(0, 4))

        # Log
        ctk.CTkLabel(right, text="Log",
                     font=ctk.CTkFont(size=12, weight="bold")
                     ).grid(row=12, column=0, padx=12, pady=(4, 2), sticky="w")
        self._log_text = ctk.CTkTextbox(
            right, height=110,
            font=ctk.CTkFont(size=10, family="monospace"),
            state="disabled", corner_radius=6)
        self._log_text.grid(row=13, column=0, sticky="nsew", padx=8, pady=(0, 10))

    def _metric(self, parent, label, value, col):
        card = ctk.CTkFrame(parent, corner_radius=6)
        card.grid(row=0, column=col, padx=6, pady=8, sticky="ew")
        ctk.CTkLabel(card, text=label, font=ctk.CTkFont(size=10),
                     text_color="#888888").pack(pady=(6, 0))
        lbl = ctk.CTkLabel(card, text=value,
                            font=ctk.CTkFont(size=18, weight="bold"))
        lbl.pack(pady=(2, 6))
        return lbl

    # ── Global Config tab ─────────────────────────────────────────

    def _build_config_tab(self, parent):
        from . import config as cfg_module_inner
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(0, weight=1)

        scroll = ctk.CTkScrollableFrame(parent, corner_radius=0)
        scroll.grid(row=0, column=0, sticky="nsew", padx=16, pady=16)
        scroll.grid_columnconfigure(1, weight=1)

        self._cfg_vars = {}

        def section(text, r):
            ctk.CTkLabel(scroll, text=text,
                         font=ctk.CTkFont(size=12, weight="bold"),
                         text_color="#888888"
                         ).grid(row=r, column=0, columnspan=2,
                                padx=4, pady=(16, 4), sticky="w")

        def hint_label(text, r):
            ctk.CTkLabel(scroll, text=text,
                         font=ctk.CTkFont(size=10), text_color="#888888",
                         anchor="w", wraplength=540, justify="left"
                         ).grid(row=r, column=1, sticky="w", padx=4, pady=(0, 6))

        def field(label, key, r, browse=False, is_dir=False):
            ctk.CTkLabel(scroll, text=label, font=ctk.CTkFont(size=12),
                         anchor="w", width=175
                         ).grid(row=r, column=0, padx=(4, 8), pady=(6, 0), sticky="nw")
            var = tk.StringVar(value=str(self._cfg.get(key, "")))
            self._cfg_vars[key] = var
            f = ctk.CTkFrame(scroll, fg_color="transparent")
            f.grid(row=r, column=1, sticky="ew", pady=(6, 0))
            f.grid_columnconfigure(0, weight=1)
            ctk.CTkEntry(f, textvariable=var,
                         font=ctk.CTkFont(size=11, family="monospace")
                         ).grid(row=0, column=0, sticky="ew")
            if browse:
                ctk.CTkButton(f, text="Browse", width=64,
                              command=lambda k=key, v=var, d=is_dir:
                                  self._browse_cfg_path(k, v, d),
                              font=ctk.CTkFont(size=11)
                              ).grid(row=0, column=1, padx=(4, 0))
            desc = cfg_module_inner.DESCRIPTIONS.get(key, "")
            if desc:
                hint_label(desc, r + 1)



        r = 0
        section("SPARSH installation", r); r += 1
        field("SPARSH folder",   "sparsh_location", r, browse=True, is_dir=True); r += 2

        section("Conda environment", r); r += 1
        field("Conda env name",  "conda_env",  r); r += 2
        field("conda.sh path",   "conda_sh",   r, browse=True); r += 2

        section("Analysis options  (all optional)", r); r += 1
        field("CN panel file",    "panel",         r, browse=True); r += 2
        field("BLAST % identity", "blast_percent", r);              r += 2

        section("Watcher settings", r); r += 1
        field("Watch interval (s)",  "watch_interval",  r); r += 2
        field("BAM stable wait (s)", "bam_stable_wait", r); r += 2

        btn_row = ctk.CTkFrame(parent, fg_color="transparent")
        btn_row.grid(row=1, column=0, pady=8, sticky="e", padx=16)
        ctk.CTkButton(btn_row, text="Reset to defaults",
                      fg_color="transparent", border_width=1,
                      text_color=("gray10", "gray90"),
                      command=self._reset_config,
                      font=ctk.CTkFont(size=12)).pack(side="left", padx=4)
        ctk.CTkButton(btn_row, text="Save config",
                      command=self._save_config,
                      font=ctk.CTkFont(size=12)).pack(side="left", padx=4)

    # ── Status bar ────────────────────────────────────────────────

    def _build_statusbar(self):
        bar = ctk.CTkFrame(self, height=26, corner_radius=0)
        bar.grid(row=2, column=0, sticky="ew")
        bar.grid_columnconfigure(3, weight=1)

        self._status_watch = ctk.CTkLabel(bar, text="Not watching",
                                          font=ctk.CTkFont(size=11), text_color="#888888")
        self._status_watch.grid(row=0, column=0, padx=12, pady=3, sticky="w")
        self._status_bam_pass = ctk.CTkLabel(bar, text="",
                                             font=ctk.CTkFont(size=11, family="monospace"),
                                             text_color="#888888")
        self._status_bam_pass.grid(row=0, column=1, padx=8, pady=3, sticky="w")
        self._status_counts = ctk.CTkLabel(bar, text="", font=ctk.CTkFont(size=11))
        self._status_counts.grid(row=0, column=2, padx=12, pady=3)
        self._status_scan = ctk.CTkLabel(bar, text="", font=ctk.CTkFont(size=11),
                                         text_color="#888888")
        self._status_scan.grid(row=0, column=4, padx=12, pady=3, sticky="e")

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
    def _set_label(widget, text: str, color: Optional[str] = None):
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
                            STATUS_COLORS.get(s.status, "#888888"))
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
        "running": ("#4FA84F", "Running"),
        "queued":  ("#E0A050", "Queued"),
        "ok":      ("#378ADD", "Done ✓"),
        "failed":  ("#E24B4A", "Failed ✗"),
        "ready":   ("#4FA84F", "Ready"),
        "short":   ("#E0A050", "Not ready"),
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
                header += f"  ({act['running']})"
            header += f"\nBAM links: {act.get('links_dir', '')}"
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
                header = (f"Waiting for BAM set {k}: file {k} (with its .bai) from every barcode, "
                          "since predict.py and report.py need all of them.")
                rows = [("ready", n, f"Set {k} ready") for n in wait["ready"]] \
                    + [("short", n, f"{have} of {k} files ready") for n, have in wait["short"]]

        barcodes = {s.name: s.barcode_dir for s in samples}
        sig = (header, tuple(rows), tuple(sorted(barcodes.items())))
        if sig == self._sig_active:
            return
        self._sig_active = sig
        for w in self._active_frame.winfo_children():
            w.destroy()
        ctk.CTkLabel(self._active_frame, text=header, anchor="w", justify="left",
                     wraplength=440,
                     font=ctk.CTkFont(size=11, weight="bold" if rows else "normal"),
                     text_color=None if rows else "#888888"
                     ).pack(fill="x", padx=8, pady=(4, 2))
        for kind, name, label in rows:
            color, default_label = self._ROW_STYLE[kind]
            fr = ctk.CTkFrame(self._active_frame, corner_radius=4, fg_color=("gray90", "gray20"))
            fr.pack(fill="x", pady=2, padx=2)
            fr.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(fr, text="●", text_color=color, font=ctk.CTkFont(size=12)
                         ).grid(row=0, column=0, padx=(8, 4), pady=2)
            ctk.CTkLabel(fr, text=f"{name}  /  {barcodes.get(name, '—')}", anchor="w",
                         font=ctk.CTkFont(size=11, weight="bold")
                         ).grid(row=0, column=1, sticky="w", pady=2)
            ctk.CTkLabel(fr, text=label or default_label, font=ctk.CTkFont(size=11),
                         text_color=color
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
                         font=ctk.CTkFont(size=11), text_color="#888888"
                         ).pack(anchor="w", padx=8, pady=6)
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
                result += " · LTS copy failed"
            if r["report_ok"] and not r["failed"] and r["lts_ok"] is not False:
                color = "#4FA84F"
            elif r["report_ok"]:
                color = "#E0A050"
            else:
                color = "#E24B4A"
            when = time.strftime("%H:%M", time.localtime(r["finished"]))
            samples_txt = "   ".join([f"{n} ✓" for n in r["ok"]] + [f"{n} ✗" for n in r["failed"]])

            fr = ctk.CTkFrame(self._done_frame, corner_radius=4, fg_color=("gray90", "gray20"))
            fr.pack(fill="x", pady=2, padx=2)
            fr.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(fr, text="●", text_color=color, font=ctk.CTkFont(size=12)
                         ).grid(row=0, column=0, rowspan=3, padx=(8, 4))
            ctk.CTkLabel(fr, text=f"Iteration {r['number']}  ·  {when}  ·  {result}", height=20,
                         font=ctk.CTkFont(size=11, weight="bold"), anchor="w"
                         ).grid(row=0, column=1, sticky="w", pady=(2, 0))
            ctk.CTkLabel(fr, text=samples_txt, font=ctk.CTkFont(size=11), height=18,
                         text_color="#888888", anchor="w"
                         ).grid(row=1, column=1, sticky="w")
            ctk.CTkLabel(fr, text=f"BAM links: {r['links_dir']}", height=18,
                         font=ctk.CTkFont(size=10, family="monospace"),
                         text_color="#888888", anchor="w", wraplength=380, justify="left"
                         ).grid(row=2, column=1, sticky="w", pady=(0, 2))
            ctk.CTkButton(fr, text="Open", width=52, height=22, font=ctk.CTkFont(size=11),
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
                ctk.CTkLabel(self._bam_scroll,
                             text="No BAM files detected yet in the barcode folder.",
                             font=ctk.CTkFont(size=11), text_color="#888888").pack(pady=16)
            for i, bf in enumerate(s.bam_files, start=1):
                if bf.ready and i <= s.files_analysed:
                    color, tag = "#378ADD", "analysed"
                elif bf.ready:
                    color, tag = "#4FA84F", "ready"
                else:
                    color, tag = "#E0A050", bf.note or "not ready"
                fr = ctk.CTkFrame(self._bam_scroll, corner_radius=4, fg_color="transparent")
                fr.pack(fill="x", pady=1)
                ctk.CTkLabel(fr, text="●", text_color=color, font=ctk.CTkFont(size=10)
                             ).pack(side="left", padx=(4, 6))
                ctk.CTkLabel(fr, text=tag, font=ctk.CTkFont(size=10), text_color=color,
                             width=78).pack(side="right", padx=(0, 6))
                ctk.CTkLabel(fr, text=self._fmt_bytes(bf.size_bytes), font=ctk.CTkFont(size=11),
                             text_color="#888888", width=60).pack(side="right")
                shown = bf.name if len(bf.name) <= 38 else bf.name[:24] + "…" + bf.name[-13:]
                ctk.CTkLabel(fr, text=f"{i:>2}  {shown}",
                             font=ctk.CTkFont(size=11, family="monospace"),
                             anchor="w").pack(side="left", fill="x", expand=True)

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
            lts_txt = "not configured"
        else:
            lts_txt = str(Path(lts) / out_name / str(k) / name) if k and out_name else "—"
        self._set_label(self._out_label_local, f"Local : {local_txt}")
        self._set_label(self._out_label_lts, f"LTS   : {lts_txt}")
        self._set_label(self._out_label_links, f"BAMs  : {links_txt}")

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
                self._set_label(self._status_watch, "Watching", "#4FA84F")
                self._set_label(self._status_bam_pass, f"bam_pass: {info['bam_pass']}")
            else:
                self._set_label(self._status_watch, "Looking for bam_pass…", "#E0A050")
                self._set_label(self._status_bam_pass, f"in {info.get('run_folder', '')}")
        analysing = sum(1 for s in samples if s.status == SampleStatus.ANALYSING)
        queued = sum(1 for s in samples if s.status == SampleStatus.QUEUED)
        self._set_label(self._status_counts,
                        f"{len(samples)} samples  |  {analysing} analysing  |  {queued} queued")
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
        frame = ctk.CTkFrame(self._sample_scroll, corner_radius=4, fg_color="transparent")
        frame.pack(fill="x", pady=1)

        def select(_event=None, name=sample_name):
            self._select_sample(name)

        frame.bind("<Button-1>", select)

        def lbl(text, width, bold=False, mono=False, color=None):
            font = ctk.CTkFont(size=11, weight="bold" if bold else "normal",
                               family="monospace" if mono else "default")
            kw = {"text_color": color} if color else {}
            l = ctk.CTkLabel(frame, text=text, font=font, width=width, anchor="w", **kw)
            l.pack(side="left", padx=1)
            l.bind("<Button-1>", select)
            return l

        lbl(s.name, 90, bold=True)
        lbl(s.barcode_dir, 76, mono=True)
        lbl(s.sex or "—", 36)
        lbl(f"{s.blast_percent}%" if s.blast_percent else "—", 48, color="#888888")
        lbl(str(s.max_repeats) if s.max_repeats > 0 else "unlim", 52, color="#888888")
        runs_lbl = lbl("0", 40)
        tput_lbl = lbl("—", 48)
        status_lbl = lbl("Waiting", 80, color="#888888")

        ctk.CTkButton(frame, text="X", width=24, height=20,
                      fg_color="transparent", text_color="#888888",
                      hover_color=("gray80", "gray30"),
                      command=lambda n=sample_name: self._remove_sample(n),
                      font=ctk.CTkFont(size=10)).pack(side="right", padx=4)

        self._sample_rows[sample_name] = {"frame": frame, "tput": tput_lbl,
                                          "status": status_lbl, "runs": runs_lbl}

    def _select_sample(self, name: str):
        for r in self._sample_rows.values():
            r["frame"].configure(fg_color="transparent")
        row = self._sample_rows.get(name)
        if row:
            row["frame"].configure(fg_color=("gray85", "gray25"))
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
        self._set_label(self._out_label_local, "Local : —")
        self._set_label(self._out_label_lts, "LTS   : —")
        self._set_label(self._out_label_links, "BAMs  : —")
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
        loc = self._cfg.get("sparsh_location", "").strip()
        if not loc:
            problems.append("SPARSH folder is not set (Global Config).")
        else:
            missing = [rel for rel in SPARSH_FILES.values()
                       if not (Path(loc).expanduser() / rel).exists()]
            if missing:
                problems.append("Missing in the SPARSH folder: " + ", ".join(missing))
        conda_sh = self._cfg.get("conda_sh", "").strip()
        if not conda_sh or not Path(conda_sh).expanduser().is_file():
            problems.append("conda.sh not found (Global Config).")
        panel = self._cfg.get("panel", "").strip()
        if panel and not Path(panel).expanduser().exists():
            problems.append(f"CN panel file not found: {panel}")
        return problems

    def _start_analysis(self):
        if self._running:
            return
        problems = self._setup_problems()
        if problems:
            messagebox.showwarning("Can't start yet", "\n\n".join(problems))
            return
        cfg = self._build_run_cfg()
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
        cfg_module.save(self._cfg)

        self._run_cfg = cfg
        self._started_outputs.add(self._output_key(cfg))
        app_state.set_run_folder(str(Path(cfg["run_folder"]).expanduser()), cfg["local_output"])
        runner.start(cfg)
        self._watcher = BamWatcher(watch_interval=self._cfg_int("watch_interval", 30),
                                   bam_stable_wait=self._cfg_int("bam_stable_wait", 60),
                                   on_scan_complete=runner.poll)
        self._watcher.start()
        self._running = True
        self._start_btn.configure(state="disabled", text="Running…", fg_color="#378ADD")
        self._stop_btn.configure(state="normal", fg_color="#E24B4A", hover_color="#A32D2D")
        self._sig_active = None

    def _stop_analysis(self):
        if not self._running:
            return
        if self._watcher:
            self._watcher.stop()
            self._watcher = None
        runner.stop()
        self._running = False
        self._start_btn.configure(state="normal", text="Start Analysis",
                                  fg_color="#4FA84F", hover_color="#3A8A3A")
        self._stop_btn.configure(state="disabled", fg_color="#888888", hover_color="#E24B4A")
        self._set_label(self._status_watch, "Stopped", "#E24B4A")
        self._set_label(self._status_bam_pass, "")
        self._sig_active = None

    def _browse_run_folder(self):
        d = filedialog.askdirectory(title="Select run folder")
        if d:
            self._run_folder_var.set(d)

    def _browse_folder(self, var: tk.StringVar, title: str):
        d = filedialog.askdirectory(title=title)
        if d:
            var.set(d)

    def _browse_cfg_path(self, key: str, var: tk.StringVar, is_dir: bool = False):
        if is_dir:
            d = filedialog.askdirectory(title=f"Select folder for {key}")
            if d:
                var.set(d)
        else:
            f = filedialog.askopenfilename(title=f"Select file for {key}")
            if f:
                var.set(f)

    def _add_sample(self):
        name = self._new_sample_var.get().strip()
        barcode_raw = self._new_barcode_var.get().strip()
        sex = self._new_sex_var.get().strip()
        blast = self._new_blast_var.get().strip()

        if not name or not barcode_raw:
            self._add_err_var.set("Sample ID and Barcode are required.")
            return
        if not SAMPLE_ID_RE.fullmatch(name):
            self._add_err_var.set("Sample ID can only use letters, numbers, '.', '_' and '-'.")
            return
        barcode = normalise_barcode(barcode_raw)
        if barcode is None:
            self._add_err_var.set("Barcode must be a number from 01 to 99.")
            return
        owner = app_state.barcode_owner(barcode)
        if owner:
            self._add_err_var.set(f"barcode{barcode} is already used by {owner}.")
            return
        try:
            max_rep = int(self._new_repeats_var.get().strip() or "0")
            if max_rep < 0:
                raise ValueError
        except ValueError:
            self._add_err_var.set("Max runs must be a whole number (0 = unlimited).")
            return
        if blast:
            try:
                bv = int(blast)
                if not 0 <= bv <= 100:
                    raise ValueError
                blast = str(bv)
            except ValueError:
                self._add_err_var.set("Blast % must be a whole number between 0 and 100.")
                return
        if not app_state.add_sample(name, barcode, sex, blast, max_rep):
            self._add_err_var.set(f"Sample '{name}' already exists.")
            return

        self._add_err_var.set("")
        self._new_sample_var.set("")
        self._new_barcode_var.set("")
        self._new_sex_var.set("")
        self._new_blast_var.set("")
        self._new_repeats_var.set("0")
        self._add_sample_row(name)

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

    def _save_config(self):
        for key, var in self._cfg_vars.items():
            val = var.get().strip()
            if key in ("watch_interval", "bam_stable_wait"):
                try:
                    val = int(val)
                    if val < 1:
                        raise ValueError
                except ValueError:
                    messagebox.showerror("Invalid value", f"'{key}' must be a whole number above 0.")
                    return
            self._cfg[key] = val
        cfg_module.save(self._cfg)
        if self._running:
            # Paths chosen at Start stay; global settings apply from the next command.
            self._run_cfg.update({k: v for k, v in self._cfg.items() if not k.startswith("last_")})
            runner.set_config(self._run_cfg)
            messagebox.showinfo("Saved", "Config saved. Changes apply from the next command; "
                                         "watcher timings apply after Stop and Start.")
        else:
            messagebox.showinfo("Saved", "Config saved successfully.")

    def _reset_config(self):
        if messagebox.askyesno("Reset", "Reset all config fields to defaults?"):
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
        self.destroy()
