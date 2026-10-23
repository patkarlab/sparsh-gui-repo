"""
gui.py
Main CustomTkinter GUI window for the SPARSH ONT Run Manager.
"""

import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox
from typing import Optional

import customtkinter as ctk

from . import config as cfg_module
from .runner import runner
from .state import SampleStatus, app_state
from .watcher import BamWatcher

# ── Theme ──────────────────────────────────────────────────────────────────
ctk.set_appearance_mode("System")
ctk.set_default_color_theme("blue")

STATUS_COLORS = {
    SampleStatus.WAITING:    ("#888888", "#666666"),
    SampleStatus.SEQUENCING: ("#E0A050", "#C07A20"),
    SampleStatus.ANALYSING:  ("#4FA84F", "#3A8A3A"),
    SampleStatus.DONE:       ("#378ADD", "#185FA5"),
    SampleStatus.ERROR:      ("#E24B4A", "#A32D2D"),
}
STATUS_LABELS = {
    SampleStatus.WAITING:    "Waiting",
    SampleStatus.SEQUENCING: "Sequencing…",
    SampleStatus.ANALYSING:  "Analysing",
    SampleStatus.DONE:       "Done",
    SampleStatus.ERROR:      "Error",
}


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("SPARSH — ONT Run Manager")
        self.geometry("1100x680")
        self.minsize(900, 580)

        self._cfg = cfg_module.load()
        self._selected_sample: Optional[str] = None
        self._watcher: Optional[BamWatcher] = None
        self._sample_rows: dict = {}   # sample_name -> dict of row widgets

        self._build_ui()
        self._start_watcher()
        self._start_refresh_loop()

    # ══════════════════════════════════════════════════════════════
    # UI construction
    # ══════════════════════════════════════════════════════════════

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # ── Top bar ──────────────────────────────────────────────
        topbar = ctk.CTkFrame(self, height=44, corner_radius=0)
        topbar.grid(row=0, column=0, sticky="ew")
        topbar.grid_columnconfigure(3, weight=1)

        ctk.CTkLabel(topbar, text="Run", font=ctk.CTkFont(size=12)).grid(row=0, column=0, padx=(12,4), pady=8)
        self._run_name_var = tk.StringVar(value="run_2026-10-23_RUN001")
        ctk.CTkEntry(topbar, textvariable=self._run_name_var, width=200,
                     font=ctk.CTkFont(size=12)).grid(row=0, column=1, padx=4, pady=8)

        ctk.CTkLabel(topbar, text="BAM root", font=ctk.CTkFont(size=12)).grid(row=0, column=2, padx=(12,4), pady=8)
        self._bam_root_var = tk.StringVar(value=self._cfg.get("bam_root",""))
        bam_entry = ctk.CTkEntry(topbar, textvariable=self._bam_root_var, width=220,
                                 font=ctk.CTkFont(size=11, family="monospace"))
        bam_entry.grid(row=0, column=3, padx=4, pady=8, sticky="ew")
        ctk.CTkButton(topbar, text="…", width=28, command=self._browse_bam_root,
                      font=ctk.CTkFont(size=12)).grid(row=0, column=4, padx=(0,8), pady=8)

        ctk.CTkLabel(topbar, text="Output root", font=ctk.CTkFont(size=12)).grid(row=0, column=5, padx=(4,4), pady=8)
        self._out_root_var = tk.StringVar(value=self._cfg.get("output_root",""))
        ctk.CTkEntry(topbar, textvariable=self._out_root_var, width=180,
                     font=ctk.CTkFont(size=11, family="monospace")).grid(row=0, column=6, padx=4, pady=8)
        ctk.CTkButton(topbar, text="…", width=28, command=self._browse_out_root,
                      font=ctk.CTkFont(size=12)).grid(row=0, column=7, padx=(0,4), pady=8)

        ctk.CTkButton(topbar, text="Apply", width=60, command=self._apply_run_info,
                      font=ctk.CTkFont(size=12)).grid(row=0, column=8, padx=(4,12), pady=8)

        # ── Tab view ─────────────────────────────────────────────
        self._tabs = ctk.CTkTabview(self, corner_radius=0)
        self._tabs.grid(row=1, column=0, sticky="nsew")
        self._tabs.add("Active run")
        self._tabs.add("Global config")

        self._build_run_tab(self._tabs.tab("Active run"))
        self._build_config_tab(self._tabs.tab("Global config"))

        # ── Status bar ───────────────────────────────────────────
        statusbar = ctk.CTkFrame(self, height=26, corner_radius=0)
        statusbar.grid(row=2, column=0, sticky="ew")
        statusbar.grid_columnconfigure(4, weight=1)

        self._status_watching = ctk.CTkLabel(statusbar, text="● Not watching",
                                             font=ctk.CTkFont(size=11), text_color="#888888")
        self._status_watching.grid(row=0, column=0, padx=12, pady=4, sticky="w")
        self._status_counts = ctk.CTkLabel(statusbar, text="0 samples",
                                           font=ctk.CTkFont(size=11))
        self._status_counts.grid(row=0, column=1, padx=12, pady=4)
        self._status_scan = ctk.CTkLabel(statusbar, text="", font=ctk.CTkFont(size=11),
                                         text_color="#888888")
        self._status_scan.grid(row=0, column=5, padx=12, pady=4, sticky="e")

    # ── Active run tab ───────────────────────────────────────────

    def _build_run_tab(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_columnconfigure(1, weight=1)
        parent.grid_rowconfigure(0, weight=1)

        # Left: sample list + add form
        left = ctk.CTkFrame(parent, corner_radius=8)
        left.grid(row=0, column=0, sticky="nsew", padx=(8,4), pady=8)
        left.grid_rowconfigure(1, weight=1)
        left.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(left, text="Samples", font=ctk.CTkFont(size=13, weight="bold")).grid(
            row=0, column=0, padx=12, pady=(10,4), sticky="w")

        # Column headers
        hdr = ctk.CTkFrame(left, fg_color="transparent")
        hdr.grid(row=1, column=0, sticky="ew", padx=8)
        for i, (col, w) in enumerate([("Sample", 80), ("Barcode", 90), ("Sex", 40),
                                       ("GB/hr", 55), ("Status", 90)]):
            ctk.CTkLabel(hdr, text=col, font=ctk.CTkFont(size=11),
                         text_color="#888888", width=w, anchor="w").grid(row=0, column=i, padx=2, pady=2)

        # Scrollable sample list
        self._sample_scroll = ctk.CTkScrollableFrame(left, corner_radius=0)
        self._sample_scroll.grid(row=2, column=0, sticky="nsew", padx=8, pady=4)
        left.grid_rowconfigure(2, weight=1)

        # Add sample form
        add_frame = ctk.CTkFrame(left, corner_radius=0, fg_color="transparent")
        add_frame.grid(row=3, column=0, sticky="ew", padx=8, pady=(4,10))
        add_frame.grid_columnconfigure(0, weight=1)
        add_frame.grid_columnconfigure(1, weight=1)

        self._new_sample_var = tk.StringVar()
        self._new_barcode_var = tk.StringVar()
        self._new_sex_var = tk.StringVar(value="F")
        self._add_err_var = tk.StringVar()

        ctk.CTkEntry(add_frame, textvariable=self._new_sample_var,
                     placeholder_text="Sample name  e.g. S05",
                     font=ctk.CTkFont(size=12)).grid(row=0, column=0, padx=(0,4), pady=4, sticky="ew")
        ctk.CTkEntry(add_frame, textvariable=self._new_barcode_var,
                     placeholder_text="barcode05", width=90,
                     font=ctk.CTkFont(size=12)).grid(row=0, column=1, padx=(0,4), pady=4, sticky="ew")
        ctk.CTkOptionMenu(add_frame, variable=self._new_sex_var,
                          values=["F", "M", ""], width=60,
                          font=ctk.CTkFont(size=12)).grid(row=0, column=2, padx=(0,4), pady=4)
        ctk.CTkButton(add_frame, text="+ Add", width=60, command=self._add_sample,
                      font=ctk.CTkFont(size=12)).grid(row=0, column=3, pady=4)
        ctk.CTkLabel(add_frame, textvariable=self._add_err_var,
                     text_color="#E24B4A", font=ctk.CTkFont(size=11)).grid(
            row=1, column=0, columnspan=4, sticky="w")

        # Right: BAM files + throughput + log
        right = ctk.CTkFrame(parent, corner_radius=8)
        right.grid(row=0, column=1, sticky="nsew", padx=(4,8), pady=8)
        right.grid_rowconfigure(1, weight=1)
        right.grid_rowconfigure(3, weight=0)
        right.grid_columnconfigure(0, weight=1)

        self._bam_label = ctk.CTkLabel(right, text="BAM files — select a sample",
                                        font=ctk.CTkFont(size=13, weight="bold"))
        self._bam_label.grid(row=0, column=0, padx=12, pady=(10,4), sticky="w")

        self._bam_scroll = ctk.CTkScrollableFrame(right, corner_radius=0, height=180)
        self._bam_scroll.grid(row=1, column=0, sticky="nsew", padx=8, pady=4)
        self._bam_scroll.grid_columnconfigure(0, weight=1)

        # Throughput metrics
        metrics_frame = ctk.CTkFrame(right, corner_radius=6)
        metrics_frame.grid(row=2, column=0, sticky="ew", padx=8, pady=4)
        metrics_frame.grid_columnconfigure((0,1,2), weight=1)

        self._m_tput   = self._metric_card(metrics_frame, "This hour", "—", 0)
        self._m_files  = self._metric_card(metrics_frame, "BAM files", "—", 1)
        self._m_total  = self._metric_card(metrics_frame, "Total written", "—", 2)

        # Log
        ctk.CTkLabel(right, text="Log", font=ctk.CTkFont(size=12, weight="bold")).grid(
            row=3, column=0, padx=12, pady=(6,2), sticky="w")
        self._log_text = ctk.CTkTextbox(right, height=130, font=ctk.CTkFont(size=11, family="monospace"),
                                         state="disabled", corner_radius=6)
        self._log_text.grid(row=4, column=0, sticky="ew", padx=8, pady=(0,10))

    def _metric_card(self, parent, label: str, value: str, col: int):
        card = ctk.CTkFrame(parent, corner_radius=6)
        card.grid(row=0, column=col, padx=6, pady=8, sticky="ew")
        ctk.CTkLabel(card, text=label, font=ctk.CTkFont(size=10),
                     text_color="#888888").pack(pady=(8,0))
        val_lbl = ctk.CTkLabel(card, text=value, font=ctk.CTkFont(size=20, weight="bold"))
        val_lbl.pack(pady=(2,8))
        return val_lbl

    # ── Config tab ───────────────────────────────────────────────

    def _build_config_tab(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        scroll = ctk.CTkScrollableFrame(parent, corner_radius=0)
        scroll.grid(row=0, column=0, sticky="nsew", padx=16, pady=16)
        parent.grid_rowconfigure(0, weight=1)
        scroll.grid_columnconfigure(1, weight=1)

        fields = [
            ("Reference FASTA",   "reference"),
            ("Manifest CSV",      "manifest"),
            ("Model directory",   "model_dir"),
            ("CN panel",          "panel"),
            ("BLAST % identity",  "blast_percent"),
            ("Scripts directory", "scripts_dir"),
            ("Watch interval (s)","watch_interval"),
            ("BAM stable wait (s)","bam_stable_wait"),
        ]
        self._cfg_vars = {}
        for i, (label, key) in enumerate(fields):
            ctk.CTkLabel(scroll, text=label, font=ctk.CTkFont(size=12),
                         anchor="w").grid(row=i, column=0, padx=(8,12), pady=6, sticky="w")
            var = tk.StringVar(value=str(self._cfg.get(key, "")))
            self._cfg_vars[key] = var
            ctk.CTkEntry(scroll, textvariable=var, font=ctk.CTkFont(size=11, family="monospace")
                         ).grid(row=i, column=1, padx=(0,8), pady=6, sticky="ew")

        btn_row = ctk.CTkFrame(parent, fg_color="transparent")
        btn_row.grid(row=1, column=0, pady=8, sticky="e", padx=16)
        ctk.CTkButton(btn_row, text="Reset to defaults", fg_color="transparent",
                      border_width=1, text_color=("gray10","gray90"),
                      command=self._reset_config, font=ctk.CTkFont(size=12)).pack(side="left", padx=4)
        ctk.CTkButton(btn_row, text="Save config", command=self._save_config,
                      font=ctk.CTkFont(size=12)).pack(side="left", padx=4)

    # ══════════════════════════════════════════════════════════════
    # Watcher + refresh loop
    # ══════════════════════════════════════════════════════════════

    def _start_watcher(self):
        bam_root = self._bam_root_var.get().strip()
        if not bam_root:
            return
        self._watcher = BamWatcher(
            bam_root=bam_root,
            watch_interval=int(self._cfg.get("watch_interval", 30)),
            bam_stable_wait=int(self._cfg.get("bam_stable_wait", 60)),
            on_new_stable_bam=self._on_stable_bam,
            on_scan_complete=self._on_scan_complete,
        )
        runner.start()
        self._watcher.start()
        self._status_watching.configure(text=f"● Watching {bam_root}", text_color="#4FA84F")

    def _on_stable_bam(self, sample_name: str, bam_path: str):
        """Called from watcher thread — submit analysis job."""
        runner.submit(
            sample_name=sample_name,
            bam_dir=str(self._watcher.bam_root / app_state.get_sample(sample_name).barcode),
            cfg=self._build_run_cfg(),
        )

    def _on_scan_complete(self):
        scan_time = time.strftime("%H:%M:%S")
        self.after(0, lambda: self._status_scan.configure(text=f"Last scan: {scan_time}"))

    def _start_refresh_loop(self):
        """Refresh GUI every 2 seconds from the main thread."""
        self._refresh_gui()
        self.after(2000, self._start_refresh_loop)

    # ══════════════════════════════════════════════════════════════
    # GUI refresh
    # ══════════════════════════════════════════════════════════════

    def _refresh_gui(self):
        samples = app_state.get_all_samples()
        # Rebuild sample rows for any new samples
        for s in samples:
            if s.name not in self._sample_rows:
                self._add_sample_row(s.name)

        # Update each row
        for s in samples:
            row = self._sample_rows.get(s.name)
            if not row:
                continue
            tput = s.throughput_per_hour_gb()
            tput_str = f"{tput:.1f}" if tput is not None else "—"
            row["tput"].configure(text=tput_str)
            color = STATUS_COLORS.get(s.status, ("#888","#666"))[0]
            row["status"].configure(text=STATUS_LABELS.get(s.status, "?"), text_color=color)

        # Update right panel for selected sample
        if self._selected_sample:
            self._refresh_right_panel(self._selected_sample)

        # Status bar counts
        analysing = sum(1 for s in samples if s.status == SampleStatus.ANALYSING)
        sequencing = sum(1 for s in samples if s.status == SampleStatus.SEQUENCING)
        self._status_counts.configure(
            text=f"{len(samples)} samples  ·  {analysing} analysing  ·  {sequencing} sequencing"
        )

    def _refresh_right_panel(self, sample_name: str):
        sample = app_state.get_sample(sample_name)
        if not sample:
            return

        # BAM file list
        for w in self._bam_scroll.winfo_children():
            w.destroy()
        for i, bf in enumerate(sample.bam_files):
            color = "#E0A050" if bf.growing else "#4FA84F"
            tag = "growing" if bf.growing else ("indexed" if bf.indexed else "written")
            row = ctk.CTkFrame(self._bam_scroll, corner_radius=4, fg_color="transparent")
            row.pack(fill="x", pady=1)
            ctk.CTkLabel(row, text="⬤", text_color=color,
                         font=ctk.CTkFont(size=9)).pack(side="left", padx=(4,6))
            ctk.CTkLabel(row, text=bf.name,
                         font=ctk.CTkFont(size=11, family="monospace"),
                         anchor="w").pack(side="left", fill="x", expand=True)
            sz = self._fmt_bytes(bf.size_bytes)
            ctk.CTkLabel(row, text=sz, font=ctk.CTkFont(size=11),
                         text_color="#888888", width=55).pack(side="left")
            ctk.CTkLabel(row, text=tag, font=ctk.CTkFont(size=10),
                         text_color=color, width=55).pack(side="left", padx=(0,6))

        # Metrics
        tput = sample.throughput_per_hour_gb()
        self._m_tput.configure(text=(f"{tput:.1f} GB/hr" if tput is not None else "—"))
        self._m_files.configure(text=str(len(sample.bam_files)))
        self._m_total.configure(text=f"{sample.total_bam_size_gb():.1f} GB")

        # Log
        self._log_text.configure(state="normal")
        self._log_text.delete("1.0", "end")
        self._log_text.insert("end", "\n".join(sample.log_lines[-60:]))
        self._log_text.see("end")
        self._log_text.configure(state="disabled")

    def _add_sample_row(self, sample_name: str):
        s = app_state.get_sample(sample_name)
        if not s:
            return
        frame = ctk.CTkFrame(self._sample_scroll, corner_radius=4, fg_color="transparent")
        frame.pack(fill="x", pady=1)

        def select(name=sample_name, f=frame):
            for r in self._sample_rows.values():
                r["frame"].configure(fg_color="transparent")
            f.configure(fg_color=("gray85","gray25"))
            self._selected_sample = name
            self._bam_label.configure(text=f"BAM files — {name} / {app_state.get_sample(name).barcode}")
            self._refresh_right_panel(name)

        frame.bind("<Button-1>", lambda e: select())

        ctk.CTkLabel(frame, text=s.name, font=ctk.CTkFont(size=12, weight="bold"),
                     width=80, anchor="w").pack(side="left", padx=(6,2))
        ctk.CTkLabel(frame, text=s.barcode, font=ctk.CTkFont(size=11, family="monospace"),
                     width=90, anchor="w").pack(side="left", padx=2)
        ctk.CTkLabel(frame, text=s.sex or "—", font=ctk.CTkFont(size=11),
                     width=40, anchor="w").pack(side="left", padx=2)

        tput_lbl = ctk.CTkLabel(frame, text="—", font=ctk.CTkFont(size=11),
                                 width=55, anchor="w")
        tput_lbl.pack(side="left", padx=2)

        status_lbl = ctk.CTkLabel(frame, text="Waiting", font=ctk.CTkFont(size=11),
                                   text_color="#888888", width=90, anchor="w")
        status_lbl.pack(side="left", padx=2)

        del_btn = ctk.CTkButton(frame, text="✕", width=24, height=20,
                                fg_color="transparent", text_color="#888888",
                                hover_color=("gray80","gray30"),
                                command=lambda n=sample_name: self._remove_sample(n),
                                font=ctk.CTkFont(size=10))
        del_btn.pack(side="right", padx=4)

        self._sample_rows[sample_name] = {
            "frame": frame, "tput": tput_lbl, "status": status_lbl
        }

    # ══════════════════════════════════════════════════════════════
    # Actions
    # ══════════════════════════════════════════════════════════════

    def _add_sample(self):
        name = self._new_sample_var.get().strip()
        barcode = self._new_barcode_var.get().strip()
        sex = self._new_sex_var.get()
        if not name or not barcode:
            self._add_err_var.set("Sample name and barcode are required.")
            return
        self._add_err_var.set("")
        if not app_state.add_sample(name, barcode, sex):
            self._add_err_var.set(f"Sample '{name}' already exists.")
            return
        self._new_sample_var.set("")
        self._new_barcode_var.set("")
        self._add_sample_row(name)

    def _remove_sample(self, name: str):
        if messagebox.askyesno("Remove sample", f"Remove sample '{name}'?"):
            row = self._sample_rows.pop(name, None)
            if row:
                row["frame"].destroy()
            app_state.remove_sample(name)
            if self._selected_sample == name:
                self._selected_sample = None
                self._bam_label.configure(text="BAM files — select a sample")

    def _apply_run_info(self):
        bam_root = self._bam_root_var.get().strip()
        out_root = self._out_root_var.get().strip()
        run_name = self._run_name_var.get().strip()
        app_state.set_run_info(run_name, bam_root, out_root)
        self._cfg["bam_root"] = bam_root
        self._cfg["output_root"] = out_root
        cfg_module.save(self._cfg)

        # Restart watcher with new root
        if self._watcher:
            self._watcher.stop()
        if bam_root:
            self._start_watcher()

    def _browse_bam_root(self):
        d = filedialog.askdirectory(title="Select BAM root folder")
        if d:
            self._bam_root_var.set(d)

    def _browse_out_root(self):
        d = filedialog.askdirectory(title="Select output root folder")
        if d:
            self._out_root_var.set(d)

    def _save_config(self):
        for key, var in self._cfg_vars.items():
            val = var.get().strip()
            if key in ("watch_interval", "bam_stable_wait"):
                try:
                    val = int(val)
                except ValueError:
                    messagebox.showerror("Invalid value", f"'{key}' must be an integer.")
                    return
            self._cfg[key] = val
        cfg_module.save(self._cfg)
        messagebox.showinfo("Saved", "Config saved.")

    def _reset_config(self):
        if messagebox.askyesno("Reset config", "Reset all fields to defaults?"):
            for key, var in self._cfg_vars.items():
                var.set(str(cfg_module.DEFAULTS.get(key, "")))

    # ══════════════════════════════════════════════════════════════
    # Helpers
    # ══════════════════════════════════════════════════════════════

    def _build_run_cfg(self) -> dict:
        return {**self._cfg,
                "bam_root": self._bam_root_var.get().strip(),
                "output_root": self._out_root_var.get().strip()}

    @staticmethod
    def _fmt_bytes(n: int) -> str:
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024:
                return f"{n:.0f} {unit}"
            n /= 1024
        return f"{n:.1f} TB"

    def on_closing(self):
        if self._watcher:
            self._watcher.stop()
        runner.stop()
        self.destroy()
