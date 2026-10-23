"""
watcher.py
Watches the BAM root folder for new/growing BAM files.
For each sample, it polls the barcode subfolder every N seconds,
updates state, and calls the runner when a new stable BAM appears.
"""

import os
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Optional

from .state import AppState, BamFile, SampleStatus, app_state


class BamWatcher:
    """
    Spawns one background thread that scans all known sample
    barcode folders at a fixed interval.

    When a BAM file that was previously 'growing' becomes stable
    (unchanged size for `bam_stable_wait` seconds), the
    `on_new_stable_bam` callback is invoked so the runner can
    trigger SPARSH.
    """

    def __init__(
        self,
        bam_root: str,
        watch_interval: int = 30,
        bam_stable_wait: int = 60,
        on_new_stable_bam: Optional[Callable[[str, str], None]] = None,
        on_scan_complete: Optional[Callable[[], None]] = None,
    ):
        self.bam_root = Path(bam_root)
        self.watch_interval = watch_interval
        self.bam_stable_wait = bam_stable_wait
        self.on_new_stable_bam = on_new_stable_bam   # (sample_name, bam_path)
        self.on_scan_complete = on_scan_complete

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Track previous scan sizes: {sample_name: {bam_path: (size, first_seen_stable_ts)}}
        self._prev: Dict[str, Dict[str, tuple]] = {}

    # ── Public API ────────────────────────────────────────────────

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="bam-watcher")
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def update_bam_root(self, bam_root: str):
        self.bam_root = Path(bam_root)

    # ── Internal ──────────────────────────────────────────────────

    def _loop(self):
        while not self._stop_event.is_set():
            try:
                self._scan_all()
            except Exception as e:
                # Never crash the watcher thread
                print(f"[watcher] scan error: {e}")
            self._stop_event.wait(timeout=self.watch_interval)

    def _scan_all(self):
        samples = app_state.get_all_samples()
        for sample in samples:
            self._scan_sample(sample.name, sample.barcode)
        if self.on_scan_complete:
            self.on_scan_complete()

    def _scan_sample(self, sample_name: str, barcode: str):
        folder = self.bam_root / barcode
        if not folder.exists():
            return

        bam_files = []
        prev_sample = self._prev.setdefault(sample_name, {})
        now = time.time()

        for entry in sorted(folder.iterdir()):
            if entry.suffix.lower() != ".bam":
                continue
            try:
                size = entry.stat().st_size
            except OSError:
                continue

            indexed = (folder / (entry.name + ".bai")).exists() or \
                      (folder / (entry.stem + ".bai")).exists()

            prev_entry = prev_sample.get(str(entry))
            if prev_entry is None:
                # First time we see this file
                prev_sample[str(entry)] = (size, now)
                growing = True
                stable_since = None
            else:
                prev_size, stable_since = prev_entry
                if size != prev_size:
                    # Still growing
                    prev_sample[str(entry)] = (size, now)
                    growing = True
                    stable_since = None
                else:
                    growing = False
                    # File size unchanged — check if stable long enough
                    if stable_since and (now - stable_since) >= self.bam_stable_wait:
                        # Trigger analysis if not already triggered
                        self._maybe_trigger(sample_name, str(entry))

            bam_files.append(BamFile(
                path=str(entry),
                name=entry.name,
                size_bytes=size,
                growing=growing,
                indexed=indexed,
            ))

        app_state.update_bam_files(sample_name, bam_files)

    def _maybe_trigger(self, sample_name: str, bam_path: str):
        """Fire the callback once per stable BAM (tracked to avoid repeats)."""
        key = f"{sample_name}::{bam_path}::triggered"
        sample_prev = self._prev.get(sample_name, {})
        if sample_prev.get(key):
            return
        sample_prev[key] = True

        app_state.add_log(sample_name, f"BAM stable — triggering analysis: {Path(bam_path).name}")
        if self.on_new_stable_bam:
            self.on_new_stable_bam(sample_name, bam_path)
