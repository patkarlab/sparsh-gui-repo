"""
state.py — shared in-memory state used by the GUI, the watcher and the runner.
All public AppState methods are thread-safe.
"""
import os
import re
import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Dict, List, Optional, Tuple

THROUGHPUT_WINDOW_S   = 3600    # GB/hr is measured over (up to) the last hour
THROUGHPUT_MIN_SPAN_S = 600     # ...and only shown once 10 minutes of history exist
_HISTORY_KEEP_S       = THROUGHPUT_WINDOW_S + 300
LOG_LINES_KEPT        = 1000


class SampleStatus(Enum):
    WAITING    = auto()   # no BAM files yet
    SEQUENCING = auto()   # BAM files present, waiting for new ones
    QUEUED     = auto()   # new data found, waiting for its turn
    ANALYSING  = auto()   # run_sample.py running for this sample
    DONE       = auto()   # Max runs reached
    ERROR      = auto()   # last attempt failed; retried when new data arrives


@dataclass
class BamFile:
    path:       str
    name:       str
    size_bytes: int
    growing:    bool      # size changed since the previous scan
    indexed:    bool      # a .bai index sits next to it
    ready:      bool      # complete: can be linked into an iteration
    note:       str = ""  # why it is not ready yet (shown in the GUI)


_BATCH_RE = re.compile(r"_(\d+)\.bam$", re.IGNORECASE)


def bam_order_key(name: str, mtime: float):
    """MinKNOW numbers each barcode's files ..._0.bam, ..._1.bam, ...; order by that
    number, falling back to modification time for files named differently."""
    m = _BATCH_RE.search(name)
    return (0, int(m.group(1)), 0.0, name) if m else (1, 0, mtime, name)


@dataclass
class ThroughputPoint:
    timestamp:   float
    total_bytes: int


def normalise_barcode(text: str) -> Optional[str]:
    """'1', '01', 'barcode01', 'BC01', 'NB01', 'RB01' -> '01'.
    None unless it is a barcode from 1 to 99."""
    m = re.fullmatch(r"(?:barcode|bc|nb|rb)?[\s_-]*0*(\d{1,2})", text.strip().lower())
    if not m:
        return None
    n = int(m.group(1))
    return f"{n:02d}" if 1 <= n <= 99 else None


@dataclass
class Sample:
    name:          str
    barcode:       str          # two digits, e.g. "01"
    sex:           str          # "F", "M" or "" (optional)
    blast_percent: str          # "0"-"100" or "" (optional)
    max_repeats:   int          # 0 = unlimited
    status:        SampleStatus = SampleStatus.WAITING
    bam_files:     List[BamFile] = field(default_factory=list)
    throughput_history: List[ThroughputPoint] = field(default_factory=list)
    analysis_count: int = 0                         # successful analyses
    files_analysed: int = 0                         # BAM files in the last attempt
    last_round:     Optional[int] = None            # iteration of the last success
    log_lines:      List[str] = field(default_factory=list)

    @property
    def barcode_dir(self) -> str:
        return f"barcode{self.barcode}"

    def ready_count(self) -> int:
        """How many BAM files, in order from the first, are complete."""
        n = 0
        for b in self.bam_files:
            if not b.ready:
                break
            n += 1
        return n

    def link_count(self, iteration: int) -> int:
        """Files iteration k links for this sample: its first k, or all it has."""
        return min(iteration, self.ready_count())

    def throughput_per_hour_gb(self) -> Optional[float]:
        """BAM data written per hour, measured over the last hour of scans."""
        h = self.throughput_history
        if len(h) < 2:
            return None
        latest = h[-1]
        oldest = next(p for p in h if latest.timestamp - p.timestamp <= THROUGHPUT_WINDOW_S)
        span = latest.timestamp - oldest.timestamp
        if span < THROUGHPUT_MIN_SPAN_S:
            return None
        gained = max(0, latest.total_bytes - oldest.total_bytes)
        return round(gained / span * 3600 / 1024 ** 3, 2)

    def total_bam_size_gb(self) -> float:
        return round(sum(b.size_bytes for b in self.bam_files) / 1024 ** 3, 2)

    def repeats_exhausted(self) -> bool:
        return self.max_repeats > 0 and self.analysis_count >= self.max_repeats

    def add_log(self, line: str) -> None:
        self.log_lines.append(f"[{time.strftime('%H:%M:%S')}] {line}")
        if len(self.log_lines) > LOG_LINES_KEPT:
            self.log_lines = self.log_lines[-LOG_LINES_KEPT:]


def find_bam_pass(run_folder: str) -> str:
    """
    Breadth-first search, up to five folder levels below run_folder, for a directory
    named 'bam_pass'. MinKNOW usually puts it at <run>/<date>_<time>_<flowcell>_<id>/bam_pass.
    Returns "" if not found. The first one found (shallowest, then alphabetical) is used.
    """
    root = Path(run_folder)
    if not root.is_dir():
        return ""
    queue: List[Tuple[Path, int]] = [(root, 0)]
    while queue:
        current, depth = queue.pop(0)
        try:
            children = sorted(c for c in current.iterdir() if c.is_dir())
        except OSError:
            continue
        for child in children:
            if child.name == "bam_pass":
                return str(child)
            if depth < 4:
                queue.append((child, depth + 1))
    return ""


class AppState:
    def __init__(self):
        self._lock = threading.RLock()
        self._samples: Dict[str, Sample] = {}
        self._run_folder = ""
        self._bam_pass = ""
        self._output_root = ""
        self._last_scan: Optional[float] = None
        self._activity: Optional[dict] = None
        self._current_command: Optional[str] = None
        self._last_command: Optional[str] = None
        self._rounds: List[dict] = []
        self._run_complete = False      # MinKNOW wrote final_summary*.txt
        self._uses_index = False        # the run writes .bai files

    # ── Run info ──────────────────────────────────────────────────

    def set_run_folder(self, run_folder: str, output_root: str) -> str:
        """Set the run folder and look for bam_pass inside it (watcher retries if absent).
        When it is another folder than before, what the watcher found in the old one
        (each sample's BAM files and data rate) is forgotten, so that no iteration can
        start on the old run's files before the new folder has been scanned."""
        bam_pass = find_bam_pass(run_folder) if run_folder else ""
        with self._lock:
            changed = os.path.realpath(run_folder) != os.path.realpath(self._run_folder) \
                if (run_folder and self._run_folder) else bool(run_folder != self._run_folder)
            self._run_folder = run_folder
            self._bam_pass = bam_pass
            self._output_root = output_root
            self._run_complete = self._uses_index = False
            if changed:
                for s in self._samples.values():
                    s.bam_files = []
                    s.throughput_history = []
                    s.files_analysed = 0
                    if s.status != SampleStatus.DONE:
                        s.status = SampleStatus.WAITING
        return bam_pass

    def set_bam_pass(self, path: str):
        with self._lock:
            self._bam_pass = path

    def get_run_info(self) -> dict:
        with self._lock:
            return {"run_folder": self._run_folder,
                    "bam_pass": self._bam_pass,
                    "output_root": self._output_root}

    def set_run_flags(self, run_complete: bool, uses_index: bool):
        with self._lock:
            self._run_complete, self._uses_index = run_complete, uses_index

    def get_run_flags(self) -> Tuple[bool, bool]:
        with self._lock:
            return self._run_complete, self._uses_index

    def set_last_scan(self, ts: float):
        with self._lock:
            self._last_scan = ts

    def get_last_scan(self) -> Optional[float]:
        with self._lock:
            return self._last_scan

    # ── Runner progress (read by the GUI) ─────────────────────────

    def set_activity(self, activity: Optional[dict]):
        with self._lock:
            self._activity = activity

    def get_activity(self) -> Optional[dict]:
        with self._lock:
            return dict(self._activity) if self._activity else None

    def set_command(self, command: Optional[str]):
        with self._lock:
            self._current_command = command
            if command:
                self._last_command = command

    def get_commands(self) -> Tuple[Optional[str], Optional[str]]:
        with self._lock:
            return self._current_command, self._last_command

    def add_round_summary(self, summary: dict):
        with self._lock:
            self._rounds.append(summary)

    def get_round_summaries(self) -> List[dict]:
        with self._lock:
            return [dict(r) for r in self._rounds]

    # ── Samples ───────────────────────────────────────────────────

    def add_sample(self, name: str, barcode: str, sex: str,
                   blast_percent: str, max_repeats: int) -> bool:
        """False if a sample of that name exists already. Names that differ only in
        case count as the same, because predict.py upper-cases sample names."""
        with self._lock:
            if self.sample_named(name) is not None:
                return False
            self._samples[name] = Sample(name=name, barcode=barcode, sex=sex,
                                         blast_percent=blast_percent,
                                         max_repeats=max_repeats)
            return True

    def sample_named(self, name: str) -> Optional[str]:
        """The existing sample whose name matches `name` ignoring case, if any."""
        with self._lock:
            wanted = name.strip().upper()
            for existing in self._samples:
                if existing.upper() == wanted:
                    return existing
            return None

    def barcode_owner(self, barcode: str) -> Optional[str]:
        with self._lock:
            for s in self._samples.values():
                if s.barcode == barcode:
                    return s.name
            return None

    def remove_sample(self, name: str):
        with self._lock:
            self._samples.pop(name, None)

    def get_sample(self, name: str) -> Optional[Sample]:
        with self._lock:
            return self._samples.get(name)

    def get_all_samples(self) -> List[Sample]:
        with self._lock:
            return list(self._samples.values())

    def update_bam_files(self, name: str, bam_files: List[BamFile]):
        with self._lock:
            s = self._samples.get(name)
            if not s:
                return
            s.bam_files = bam_files
            now = time.time()
            s.throughput_history.append(
                ThroughputPoint(now, sum(b.size_bytes for b in bam_files)))
            cutoff = now - _HISTORY_KEEP_S
            while len(s.throughput_history) > 2 and s.throughput_history[0].timestamp < cutoff:
                s.throughput_history.pop(0)
            if s.status == SampleStatus.WAITING and bam_files:
                s.status = SampleStatus.SEQUENCING

    def set_status(self, name: str, status: SampleStatus):
        with self._lock:
            s = self._samples.get(name)
            if s:
                s.status = status

    def reset_idle(self, name: str):
        """Queued/analysing -> back to waiting for data (used when analysis is stopped)."""
        with self._lock:
            s = self._samples.get(name)
            if s and s.status in (SampleStatus.QUEUED, SampleStatus.ANALYSING):
                s.status = SampleStatus.SEQUENCING if s.bam_files else SampleStatus.WAITING

    def record_attempt(self, name: str, files_linked: int, ok: bool, iteration: int):
        """Store the outcome of one run_sample.py attempt."""
        with self._lock:
            s = self._samples.get(name)
            if not s:
                return
            s.files_analysed = files_linked
            if ok:
                s.analysis_count += 1
                s.last_round = iteration
                s.status = SampleStatus.DONE if s.repeats_exhausted() else SampleStatus.SEQUENCING
            else:
                s.status = SampleStatus.ERROR

    def add_log(self, name: str, line: str):
        with self._lock:
            s = self._samples.get(name)
            if s:
                s.add_log(line)


app_state = AppState()
