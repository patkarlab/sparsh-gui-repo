"""
runner.py
Manages SPARSH analysis subprocesses.
One job runs per sample at a time (queued if already running).
Logs stdout/stderr back into app_state.
"""

import queue
import subprocess
import threading
from pathlib import Path
from typing import Optional

from .state import SampleStatus, app_state


class AnalysisJob:
    def __init__(self, sample_name: str, bam_dir: str, cfg: dict):
        self.sample_name = sample_name
        self.bam_dir = bam_dir
        self.cfg = cfg


class Runner:
    """
    Background thread that drains a job queue and runs SPARSH.
    Multiple Runner instances can exist (one per CPU core if desired),
    but one is sufficient for typical ONT workloads.
    """

    def __init__(self):
        self._queue: queue.Queue = queue.Queue()
        self._active: dict = {}          # sample_name -> Thread
        self._lock = threading.Lock()
        self._stop_event = threading.Event()

    def start(self):
        self._stop_event.clear()

    def stop(self):
        self._stop_event.set()

    def submit(self, sample_name: str, bam_dir: str, cfg: dict):
        """Queue a job. If the sample is already running, the job is dropped (will retry next BAM)."""
        with self._lock:
            if sample_name in self._active:
                app_state.add_log(sample_name, "Analysis already running — will retry on next BAM")
                return
            t = threading.Thread(
                target=self._run_job,
                args=(sample_name, bam_dir, cfg),
                daemon=True,
                name=f"runner-{sample_name}",
            )
            self._active[sample_name] = t
            t.start()

    def _run_job(self, sample_name: str, bam_dir: str, cfg: dict):
        sample = app_state.get_sample(sample_name)
        if not sample:
            return

        # Build output directory: <output_root>/<sample_name>
        out_dir = str(Path(cfg["output_root"]) / sample_name)

        cmd = [
            "python",
            str(Path(cfg["scripts_dir"]) / "run_sample.py"),
            "--sample", sample_name,
            "--bam", bam_dir,
            "--out_dir", out_dir,
            "--reference", cfg["reference"],
            "--manifest", cfg["manifest"],
            "--model_dir", cfg["model_dir"],
            "--panel", cfg["panel"],
            "--sex", sample.sex or "F",
            "--blast_percent", str(cfg["blast_percent"]),
        ]

        app_state.set_status(sample_name, SampleStatus.ANALYSING)
        app_state.add_log(sample_name, "Starting SPARSH: " + " ".join(cmd))

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            for line in proc.stdout:
                app_state.add_log(sample_name, line.rstrip())
            proc.wait()

            if proc.returncode == 0:
                app_state.set_status(sample_name, SampleStatus.DONE)
                app_state.add_log(sample_name, "Analysis complete (exit 0)")
            else:
                app_state.set_status(sample_name, SampleStatus.ERROR)
                app_state.add_log(sample_name, f"Analysis failed (exit {proc.returncode})")

        except FileNotFoundError:
            app_state.set_status(sample_name, SampleStatus.ERROR)
            app_state.add_log(sample_name, "ERROR: python or run_sample.py not found — check Scripts dir in config")
        except Exception as e:
            app_state.set_status(sample_name, SampleStatus.ERROR)
            app_state.add_log(sample_name, f"ERROR: {e}")
        finally:
            with self._lock:
                self._active.pop(sample_name, None)


# Global singleton
runner = Runner()
