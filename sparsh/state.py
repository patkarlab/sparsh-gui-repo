"""
state.py
Single shared state object for the whole app.
The GUI, watcher, and runner all read/write this;
updates are thread-safe via a lock.
"""

import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, List, Optional


class SampleStatus(Enum):
    WAITING     = auto()   # added by user, no BAM files yet
    SEQUENCING  = auto()   # BAM files seen but analysis not triggered yet
    ANALYSING   = auto()   # SPARSH subprocess running
    DONE        = auto()   # last analysis pass finished successfully
    ERROR       = auto()   # subprocess exited non-zero


@dataclass
class BamFile:
    path: str
    name: str
    size_bytes: int
    growing: bool          # True if file size changed in last scan
    indexed: bool          # True if matching .bai exists


@dataclass
class ThroughputPoint:
    timestamp: float       # time.time()
    total_bytes: int       # cumulative bytes across all BAMs for this sample


@dataclass
class Sample:
    name: str
    barcode: str
    sex: str               # "F", "M", or ""
    status: SampleStatus = SampleStatus.WAITING
    bam_files: List[BamFile] = field(default_factory=list)
    throughput_history: List[ThroughputPoint] = field(default_factory=list)
    last_analysis_time: Optional[float] = None
    log_lines: List[str] = field(default_factory=list)

    def throughput_per_hour_gb(self) -> Optional[float]:
        """
        Estimate GB/hr using the two most recent throughput points.
        Returns None if not enough data.
        """
        h = self.throughput_history
        if len(h) < 2:
            return None
        dt = h[-1].timestamp - h[-2].timestamp
        if dt <= 0:
            return None
        db = h[-1].total_bytes - h[-2].total_bytes
        if db < 0:
            db = 0
        bytes_per_sec = db / dt
        gb_per_hour = bytes_per_sec * 3600 / (1024 ** 3)
        return round(gb_per_hour, 2)

    def total_bam_size_gb(self) -> float:
        total = sum(b.size_bytes for b in self.bam_files)
        return round(total / (1024 ** 3), 2)

    def add_log(self, line: str) -> None:
        ts = time.strftime("%H:%M:%S")
        self.log_lines.append(f"[{ts}] {line}")
        if len(self.log_lines) > 500:
            self.log_lines = self.log_lines[-500:]


class AppState:
    """Central state store. All public methods are thread-safe."""

    def __init__(self):
        self._lock = threading.Lock()
        self._samples: Dict[str, Sample] = {}   # keyed by sample name
        self._run_name: str = ""
        self._bam_root: str = ""
        self._output_root: str = ""

    # ── Run metadata ──────────────────────────────────────────────

    def set_run_info(self, run_name: str, bam_root: str, output_root: str):
        with self._lock:
            self._run_name = run_name
            self._bam_root = bam_root
            self._output_root = output_root

    def get_run_info(self) -> dict:
        with self._lock:
            return {
                "run_name": self._run_name,
                "bam_root": self._bam_root,
                "output_root": self._output_root,
            }

    # ── Samples ───────────────────────────────────────────────────

    def add_sample(self, name: str, barcode: str, sex: str) -> bool:
        """Returns False if sample name already exists."""
        with self._lock:
            if name in self._samples:
                return False
            self._samples[name] = Sample(name=name, barcode=barcode, sex=sex)
            return True

    def remove_sample(self, name: str):
        with self._lock:
            self._samples.pop(name, None)

    def get_sample(self, name: str) -> Optional[Sample]:
        with self._lock:
            return self._samples.get(name)

    def get_all_samples(self) -> List[Sample]:
        with self._lock:
            return list(self._samples.values())

    def update_bam_files(self, sample_name: str, bam_files: List[BamFile]):
        with self._lock:
            s = self._samples.get(sample_name)
            if not s:
                return
            prev_total = sum(b.size_bytes for b in s.bam_files)
            s.bam_files = bam_files
            new_total = sum(b.size_bytes for b in bam_files)
            s.throughput_history.append(
                ThroughputPoint(timestamp=time.time(), total_bytes=new_total)
            )
            if len(s.throughput_history) > 120:
                s.throughput_history = s.throughput_history[-120:]
            if s.status == SampleStatus.WAITING and bam_files:
                s.status = SampleStatus.SEQUENCING

    def set_status(self, sample_name: str, status: SampleStatus):
        with self._lock:
            s = self._samples.get(sample_name)
            if s:
                s.status = status

    def add_log(self, sample_name: str, line: str):
        with self._lock:
            s = self._samples.get(sample_name)
            if s:
                s.add_log(line)


# Global singleton — import and use directly
app_state = AppState()
