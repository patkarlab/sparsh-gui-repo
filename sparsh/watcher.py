"""
watcher.py — scans bam_pass/barcodeNN/ for every sample and records, in order,
which BAM files are complete. After each scan it tells the runner to check
whether the next BAM set (and so the next iteration) can start.

A BAM file is complete ("ready") when it is not growing and
  * its .bai index exists, or
  * the run has finished (MinKNOW wrote final_summary*.txt) and the file has
    settled (size unchanged / not modified for bam_stable_wait seconds), or
  * none of the samples' barcode folders has .bai files, the file has settled,
    and a newer BAM exists in the same folder (MinKNOW has moved on to the next file).
Only the barcode folders of the samples the user added are read; other folders
in bam_pass (other barcodes, unclassified) are ignored.
If bam_pass does not exist yet, every scan looks for it again.
"""
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set

from .state import BamFile, app_state, bam_order_key, find_bam_pass


def _has_index(folder: Path) -> bool:
    try:
        return any(folder.glob("*.bai"))
    except OSError:
        return False


def run_is_complete(bam_pass: Path) -> bool:
    """MinKNOW writes final_summary_*.txt next to bam_pass when a run ends."""
    try:
        return any(bam_pass.parent.glob("final_summary*.txt"))
    except OSError:
        return False


class BamWatcher:
    def __init__(self, watch_interval: int = 30, bam_stable_wait: int = 60,
                 on_scan_complete: Optional[Callable[[], None]] = None):
        self.watch_interval   = max(1, int(watch_interval))
        self.bam_stable_wait  = max(0, int(bam_stable_wait))
        self.on_scan_complete = on_scan_complete

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._seen: Dict[str, Dict[str, list]] = {}   # sample -> path -> [size, unchanged_since]
        self._missing_reported: Set[str] = set()
        self._index_seen = False          # any .bai seen in the samples' barcode folders

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

    # ── internals ─────────────────────────────────────────────────

    def _loop(self):
        while not self._stop_event.is_set():
            try:
                self._scan_all()
            except Exception as exc:          # the watcher must never die
                print(f"[watcher] scan error: {exc}")
            self._stop_event.wait(self.watch_interval)

    def _scan_all(self):
        info = app_state.get_run_info()
        bam_pass = info.get("bam_pass", "")
        if not bam_pass and info.get("run_folder"):
            bam_pass = find_bam_pass(info["run_folder"])
            if bam_pass:
                app_state.set_bam_pass(bam_pass)
        if bam_pass:
            bp = Path(bam_pass)
            run_complete = run_is_complete(bp)
            samples = app_state.get_all_samples()
            if not self._index_seen:
                self._index_seen = any(_has_index(bp / s.barcode_dir) for s in samples)
            for sample in samples:
                if self._stop_event.is_set():
                    return
                self._scan_sample(sample.name, bp / sample.barcode_dir, run_complete)
            app_state.set_run_flags(run_complete, self._index_seen)
        app_state.set_last_scan(time.time())
        if self.on_scan_complete and not self._stop_event.is_set():
            self.on_scan_complete()

    def _scan_sample(self, name: str, folder: Path, run_complete: bool):
        if not folder.is_dir():
            if name not in self._missing_reported:
                self._missing_reported.add(name)
                app_state.add_log(name, f"Waiting for barcode folder: {folder}")
            return
        self._missing_reported.discard(name)

        entries = []
        try:
            for entry in folder.iterdir():
                if entry.suffix.lower() != ".bam":
                    continue
                try:
                    if entry.is_file():
                        entries.append((entry, entry.stat()))
                except OSError:
                    continue
        except OSError:
            return
        entries.sort(key=lambda e: bam_order_key(e[0].name, e[1].st_mtime))

        seen = self._seen.setdefault(name, {})
        now = time.time()
        files: List[BamFile] = []
        for i, (entry, st) in enumerate(entries):
            key = str(entry)
            indexed = Path(key + ".bai").exists() or entry.with_suffix(".bai").exists()
            prev = seen.get(key)
            if prev is None or prev[0] != st.st_size:
                growing = prev is not None          # size changed since the last scan
                seen[key] = [st.st_size, now]
                unchanged_for = 0.0
            else:
                growing = False
                unchanged_for = now - prev[1]
            settled = not growing and (unchanged_for >= self.bam_stable_wait
                                       or now - st.st_mtime >= self.bam_stable_wait)
            if growing:
                ready, note = False, "growing"
            elif indexed:
                ready, note = True, ""
            elif run_complete:
                ready, note = settled, "" if settled else "settling"
            elif self._index_seen:
                ready, note = False, "no .bai yet"
            elif i == len(entries) - 1:
                ready, note = False, "writing"       # newest file of a live run
            else:
                ready, note = settled, "" if settled else "settling"
            files.append(BamFile(path=key, name=entry.name, size_bytes=st.st_size,
                                 growing=growing, indexed=indexed, ready=ready, note=note))

        present = {f.path for f in files}
        for key in list(seen):
            if key not in present:
                del seen[key]
        app_state.update_bam_files(name, files)
