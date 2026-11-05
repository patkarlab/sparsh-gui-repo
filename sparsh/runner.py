"""
runner.py — runs SPARSH one sample at a time, one iteration per BAM set.

MinKNOW writes each barcode's reads into numbered BAM files (..._0.bam, _1.bam, ...),
each followed by its .bai index. BAM set k is the k-th file of every barcode, and
iteration k analyses sets 1..k:

    <local_output>/<output folder name>/<k>/      (folder name defaults to dd_mm_yyyy)
        bam_links/barcodeNN/   links to that barcode's first k BAM (+ .bai) files,
                               passed to run_sample.py as --bam
        <sample>/              run_sample.py output
        <sample>.log
        csv/  karyotype/       collected from every sample in iteration k
        sample_sheet.csv       sample, sex and blast percentage of the samples with
                               a prediction, shown in the combined report
        predictions/           predict.py   (once per iteration)
        <report files>         report.py writes to report/; moved up into this folder
        predict_report.log

Only the barcodes of the samples the user added are looked at.

Iteration k starts once every sample's barcode has a complete k-th BAM file,
however long that takes during a live run: predict.py and report.py need outputs
from every barcode. When the run has finished (MinKNOW wrote final_summary*.txt)
no more files will come, so the remaining sets run with every sample; a barcode
that has run out of files is analysed again with all of its files, so each
iteration still has outputs from every barcode. A barcode with no BAM files at
all cannot be analysed and is left out at that point.

Samples are analysed one at a time; then predict.py and report.py run once, and
the iteration folder is mirrored to long-term storage (BAM files and links are
never copied). Stop pauses: the running process group is killed and the iteration
is kept; Start resumes it in the same folder if the run and output folders are
unchanged.
"""
import csv
import os
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Deque, Dict, List, Optional, Tuple

from .paths import expand, resolve
from .state import SampleStatus, app_state

BAM_EXTENSIONS = {".bam", ".bai", ".cram", ".crai", ".csi"}
KILL_GRACE_S = 10.0                     # SIGTERM, then SIGKILL after this many seconds
SAMPLE_SHEET = "sample_sheet.csv"


# ── helpers ───────────────────────────────────────────────────────────────────

RUN_INFO_FILE = "sparsh_run_info.txt"   # records which run an output folder belongs to


def run_name_from_path(run_folder: str) -> str:
    """Last component of the run path, e.g. /data/RUN_001 -> RUN_001."""
    return Path(run_folder.strip()).name or "run"


def output_name(cfg: dict) -> str:
    """Name of the folder that holds the iterations (the 'Output folder name' box)."""
    return cfg.get("output_name", "").strip() or run_name_from_path(cfg.get("run_folder", ""))


def output_roots(cfg: dict) -> List[Path]:
    """<local_output>/<name> and, if set, <lts_output>/<name>."""
    roots = [Path(expand(cfg.get("local_output", ""))) / output_name(cfg)]
    if cfg.get("lts_output", "").strip():
        roots.append(Path(expand(cfg["lts_output"])) / output_name(cfg))
    return roots


def recorded_run(root: Path) -> Optional[str]:
    """The run folder an output folder was created for, if recorded."""
    try:
        for line in (root / RUN_INFO_FILE).read_text().splitlines():
            if line.startswith("run_folder:"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return None


def write_run_info(root: Path, run_folder: str):
    info = root / RUN_INFO_FILE
    if info.exists():
        return
    root.mkdir(parents=True, exist_ok=True)
    info.write_text(f"run_folder: {os.path.realpath(expand(run_folder))}\n"
                    f"created: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")


def _announce(message: str):
    """Also print to the terminal the app was started from (if any)."""
    try:
        print(f"[SPARSH] {message}", flush=True)
    except Exception:
        pass


def subprocess_env() -> dict:
    """
    Environment for child processes. A PyInstaller build points library and
    Tcl/Tk variables at its own bundle; give children the user's originals so
    the conda environment loads its own libraries.
    """
    env = dict(os.environ)
    bundle = getattr(sys, "_MEIPASS", "")
    if bundle:
        for var in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "TCL_LIBRARY", "TK_LIBRARY"):
            original = env.pop(var + "_ORIG", None)
            if original is not None:
                env[var] = original
            elif bundle in env.get(var, ""):
                del env[var]
    env["PYTHONUNBUFFERED"] = "1"      # stream script output to the Log panel
    return env


def highest_iteration(parents: List[Path]) -> int:
    highest = 0
    for parent in parents:
        try:
            for child in parent.iterdir():
                if child.is_dir() and child.name.isdigit():
                    highest = max(highest, int(child.name))
        except OSError:
            pass
    return highest


def _needs_copy(src: Path, dst: Path) -> bool:
    try:
        a, b = src.stat(), dst.stat()
    except FileNotFoundError:
        return True
    return a.st_size != b.st_size or abs(a.st_mtime - b.st_mtime) > 2


def mirror_without_bams(src: Path, dst: Path) -> Tuple[int, int]:
    """Copy src -> dst with the same folder structure, leaving out BAM/index files
    (and links to them) and files already up to date. Returns (copied, left_out)."""
    copied = skipped = 0
    if not src.is_dir():
        return copied, skipped
    dst.mkdir(parents=True, exist_ok=True)
    for item in sorted(src.rglob("*")):
        target = dst / item.relative_to(src)
        if item.is_dir() and not item.is_symlink():
            target.mkdir(parents=True, exist_ok=True)
        elif item.suffix.lower() in BAM_EXTENSIONS or item.is_symlink():
            skipped += 1
        elif item.is_file() and _needs_copy(item, target):
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
            copied += 1
    return copied, skipped


def move_contents_up(src: Path, dst: Path, protected: set) -> Tuple[int, List[str]]:
    """
    Move everything inside src into dst, replacing older copies of the same name
    (e.g. a report from a paused attempt). Names in `protected` are never
    replaced; those items stay in src. src is removed once it is empty.
    Returns (number moved, names left in src).
    """
    moved, kept = 0, []
    for item in sorted(src.iterdir()):
        if item.name in protected:
            kept.append(item.name)
            continue
        target = dst / item.name
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        elif target.exists() or target.is_symlink():
            target.unlink()
        shutil.move(str(item), str(target))
        moved += 1
    if not any(src.iterdir()):
        src.rmdir()
    return moved, kept


def copy_file(src: Path, dst: Path):
    if src.is_file() and _needs_copy(src, dst):
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _link(src: Path, dst: Path):
    try:
        os.symlink(os.path.abspath(src), dst)
    except OSError:
        os.link(src, dst)                # filesystem without symbolic links (same disk only)


def make_bam_links(bam_paths: List[str], links_dir: Path):
    """Fill links_dir with links to the given BAM files and their .bai indexes."""
    links_dir.mkdir(parents=True, exist_ok=True)
    for old in links_dir.iterdir():                  # links from a paused attempt
        if old.is_symlink() or old.is_file():
            old.unlink()
    for bam in bam_paths:
        src = Path(bam)
        _link(src, links_dir / src.name)
        for index in (Path(bam + ".bai"), src.with_suffix(".bai")):
            if index.is_file() and not (links_dir / index.name).exists():
                _link(index, links_dir / index.name)


def _format_command(argv: List[str]) -> str:
    """python script.py \\\n    --opt value ...  (valid bash, readable in the GUI)"""
    parts: List[str] = []
    for tok in argv:
        quoted = shlex.quote(tok)
        if tok.startswith("--") and parts:
            parts.append(" \\\n    " + quoted)
        else:
            parts.append((" " if parts else "") + quoted)
    return "".join(parts)


def _build_script(cfg: dict, argv: List[str], log_file: Path, workdir: Path) -> str:
    return "\n".join([
        "set -e",
        f"source {shlex.quote(expand(cfg.get('conda_sh', '')))}",
        f"conda activate {shlex.quote(_conda_env(cfg))}",
        f"cd {shlex.quote(str(workdir))}",
        "set -o pipefail",
        f"{_format_command(argv)} 2>&1 | tee -a {shlex.quote(str(log_file))}",
    ])


def _conda_env(cfg: dict) -> str:
    """The environment name, or its folder with ~ expanded when a path is given."""
    env = str(cfg.get("conda_env") or "sparsh").strip()
    return expand(env) if "/" in env else env


def extra_args(cfg: dict) -> List[str]:
    """The 'extra options' box split as a shell would, with ~ expanded in paths;
    a malformed box gives nothing."""
    text = str(cfg.get("extra_args", "") or "").strip()
    if not text:
        return []
    try:
        return [os.path.expanduser(tok) if tok.startswith("~") else tok for tok in shlex.split(text)]
    except ValueError:
        return []


def _terminate_group(proc: subprocess.Popen, grace: float = KILL_GRACE_S) -> threading.Thread:
    """SIGTERM the whole process group now; SIGKILL whatever is left after `grace` s.
    Returns the thread that waits for the process, so callers can join it."""
    pgid = proc.pid                     # start_new_session=True -> the shell leads its group
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass

    def _finish():
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        proc.wait()
    thread = threading.Thread(target=_finish, daemon=True, name="sparsh-kill")
    thread.start()
    return thread


def _run_key(cfg: dict) -> Tuple[str, str, str]:
    return (cfg.get("run_folder", "").strip(), cfg.get("local_output", "").strip(),
            output_name(cfg))


def _device(cfg: dict) -> str:
    return "cpu" if str(cfg.get("device", "cuda")).strip().lower() == "cpu" else "cuda"


# ── iteration bookkeeping ─────────────────────────────────────────────────────

@dataclass
class _Iteration:
    number:   int
    out_name: str                        # output folder name
    run_key:  Tuple[str, str, str]
    iter_dir: Path
    members:  List[str] = field(default_factory=list)
    pending:  Deque[str] = field(default_factory=deque)
    running:  Optional[str] = None
    ok:       List[str] = field(default_factory=list)
    failed:   List[str] = field(default_factory=list)
    phase:    str = "samples"            # "samples" -> "aggregate"
    step:     str = ""
    step_since: float = 0.0              # when the current step began (for the window)
    collected: Dict[str, str] = field(default_factory=dict)   # file -> sample


# ── runner ────────────────────────────────────────────────────────────────────

class Runner:
    def __init__(self):
        self._cv = threading.Condition(threading.RLock())
        self._accepting = False
        self._epoch = 0                  # bumped by stop(); stale work is discarded
        self._cfg: dict = {}
        self._iter: Optional[_Iteration] = None
        self._paused: Optional[_Iteration] = None
        self._next_k: Optional[int] = None          # next iteration number (lazy)
        self._wait: Optional[dict] = None           # what the next iteration waits for
        self._proc: Optional[subprocess.Popen] = None
        self._dying: List[Tuple[subprocess.Popen, threading.Thread]] = []   # stopped jobs
        self._worker: Optional[threading.Thread] = None

    # ── public API ────────────────────────────────────────────────

    def start(self, cfg: dict):
        with self._cv:
            self._cfg = dict(cfg)
            self._accepting = True
            self._epoch += 1
            paused, self._paused = self._paused, None
            if paused is not None and paused.run_key == _run_key(cfg):
                paused.pending = deque(n for n in paused.pending if app_state.get_sample(n))
                for n in paused.pending:
                    app_state.set_status(n, SampleStatus.QUEUED)
                self._iter = paused
                self._next_k = paused.number + 1
                self._log_members(paused, f"Iteration {paused.number} resumed")
            else:
                if paused is not None:
                    self._log_members(paused, f"Paused iteration {paused.number} dropped "
                                              "(run or output folder changed)")
                self._next_k = None              # continue after the folders on disk
            for root in output_roots(cfg):
                try:
                    write_run_info(root, cfg.get("run_folder", ""))
                except OSError:
                    pass                          # e.g. long-term storage not mounted; reported at copy time
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._work_loop, daemon=True,
                                                name="sparsh-runner")
                self._worker.start()
            # No _schedule() here: the first scan of the watcher calls poll(), so a new
            # iteration is opened only on what the run folder holds now.
            self._publish()
            self._cv.notify_all()

    def stop(self):
        """Kill the running job and pause the current iteration."""
        with self._cv:
            self._accepting = False
            self._epoch += 1
            proc, self._proc = self._proc, None
            it, self._iter = self._iter, None
            if it is not None:
                if it.running:
                    it.pending.appendleft(it.running)
                    it.running = None
                for n in it.pending:
                    app_state.reset_idle(n)
                self._paused = it
                self._log_members(it, f"Iteration {it.number} paused — "
                                      "press Start Analysis to resume")
            self._wait = None
            app_state.set_activity(None)
            app_state.set_command(None)
            if proc is not None:
                self._dying.append((proc, _terminate_group(proc)))
            self._cv.notify_all()

    def wait_for_jobs(self, timeout: float = KILL_GRACE_S + 2.0) -> bool:
        """Wait until the jobs stop() killed have ended. True if they all have."""
        with self._cv:
            threads = [t for _, t in self._dying]
        deadline = time.time() + timeout
        for t in threads:
            t.join(timeout=max(0.0, deadline - time.time()))
        with self._cv:
            self._dying = [(p, t) for p, t in self._dying if t.is_alive()]
            return not self._dying

    def kill_now(self):
        """SIGKILL the jobs stop() killed that are still running (used when quitting,
        when the kill thread's own SIGKILL after the grace period would not be sent)."""
        with self._cv:
            procs = [p for p, _ in self._dying]
        for proc in procs:
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass

    def poll(self):
        """Called after every watcher scan: start the next iteration if its BAM set is ready."""
        with self._cv:
            if not self._accepting:
                return
            self._schedule()
            self._publish()
            self._cv.notify_all()

    def set_config(self, cfg: dict):
        with self._cv:
            self._cfg = dict(cfg)

    def forget(self, name: str):
        """Drop a sample the user removed from any queue."""
        with self._cv:
            for it in (self._iter, self._paused):
                if it is None:
                    continue
                if name in it.pending:
                    it.pending.remove(name)
                if (name in it.members and it.running != name
                        and name not in it.ok and name not in it.failed):
                    it.members.remove(name)
            self._publish()

    def is_busy(self) -> bool:
        with self._cv:
            return self._proc is not None

    def paused_iteration(self) -> Optional[int]:
        with self._cv:
            return self._paused.number if self._paused else None

    def can_resume(self, cfg: dict) -> bool:
        """True if Start with these settings would resume the paused iteration."""
        with self._cv:
            return self._paused is not None and self._paused.run_key == _run_key(cfg)

    # ── scheduling (call with the lock held) ──────────────────────

    def _schedule(self):
        if not self._accepting:
            return
        active = [s for s in app_state.get_all_samples() if not s.repeats_exhausted()]
        run_complete, _ = app_state.get_run_flags()
        it = self._iter
        if it is not None:
            self._wait = None
            if it.phase == "samples":            # a sample added mid-iteration can join
                for s in active:
                    if s.name not in it.members and (
                            s.ready_count() >= it.number
                            or (run_complete and s.ready_count() > 0)):
                        it.members.append(s.name)
                        it.pending.append(s.name)
                        app_state.set_status(s.name, SampleStatus.QUEUED)
                        app_state.add_log(s.name, f"Joining iteration {it.number}")
            return

        k = self._next_iteration_number()
        ready = [s for s in active if s.ready_count() >= k]
        short = [s for s in active if s.ready_count() < k]
        if run_complete:
            # No more files will come: keep going while any barcode still has an
            # unanalysed set, with every sample that has files.
            go = bool(ready)
            members = [s for s in active if s.ready_count() > 0]
        else:
            go = bool(active) and not short      # live run: wait for every barcode
            members = active
        if not go:
            self._wait = {
                "set": k, "run_complete": run_complete,
                "finished": run_complete and not ready,
                "ready": [s.name for s in ready],
                "short": [(s.name, s.ready_count()) for s in short],
            }
            return
        if run_complete:
            for s in short:
                if s.ready_count() == 0:
                    app_state.add_log(s.name, f"The run has finished and {s.barcode_dir} has no "
                                              f"BAM files: left out of iteration {k}")
                else:
                    app_state.add_log(s.name, f"The run has finished and {s.barcode_dir} has no "
                                              f"BAM file {k}: analysed again with all "
                                              f"{s.ready_count()} of its files so iteration {k} "
                                              "has outputs from every barcode")
        self._wait = None
        self._open_iteration(k, [s.name for s in members])

    def _next_iteration_number(self) -> int:
        if self._next_k is None:
            self._next_k = highest_iteration(output_roots(self._cfg)) + 1
        return self._next_k

    def _open_iteration(self, k: int, names: List[str]):
        cfg = self._cfg
        name = output_name(cfg)
        iter_dir = Path(expand(cfg.get("local_output", ""))) / name / str(k)
        it = _Iteration(number=k, out_name=name, run_key=_run_key(cfg),
                        iter_dir=iter_dir, members=list(names), pending=deque(names),
                        step_since=time.time())
        self._iter = it
        self._next_k = k + 1
        for n in names:
            app_state.set_status(n, SampleStatus.QUEUED)
        self._log_members(it, f"=== Iteration {k} started — BAM links folder: "
                              f"{iter_dir / 'bam_links'} ===")
        _announce(f"Iteration {k} started — BAM links folder: {iter_dir / 'bam_links'}")

    # ── worker ────────────────────────────────────────────────────

    def _work_loop(self):
        while True:
            with self._cv:
                while not (self._accepting and self._iter is not None):
                    self._cv.wait(timeout=2.0)
                it, epoch, cfg = self._iter, self._epoch, dict(self._cfg)
                if it.phase == "samples" and it.pending:
                    job: Optional[str] = it.pending.popleft()
                    it.running, it.step, it.step_since = job, "run_sample.py", time.time()
                else:
                    job = None
                    it.phase, it.step, it.step_since = "aggregate", "finishing", time.time()
                self._publish()
            try:
                if job is not None:
                    self._run_sample(it, job, cfg, epoch)
                else:
                    self._run_aggregate(it, cfg, epoch)
            except Exception as exc:     # never let the worker die
                self._handle_unexpected(it, job, epoch, exc)

    # ── one sample ────────────────────────────────────────────────

    def _run_sample(self, it: _Iteration, name: str, cfg: dict, epoch: int):
        def log(msg: str):
            app_state.add_log(name, msg)

        sample = app_state.get_sample(name)
        if sample is None:                       # removed while queued
            with self._cv:
                if self._epoch == epoch and it.running == name:
                    it.running = None
                    if name in it.members:
                        it.members.remove(name)
                    self._publish()
            return

        bams = [b.path for b in sample.bam_files[:sample.link_count(it.number)]]
        paths = resolve(cfg)
        sample_out = it.iter_dir / name
        log_file = it.iter_dir / f"{name}.log"
        links_dir = it.iter_dir / "bam_links" / sample.barcode_dir

        with self._cv:
            if self._epoch != epoch:
                return
            app_state.set_status(name, SampleStatus.ANALYSING)

        ok = False
        if not paths.ok:
            for problem in paths.problems:
                log(f"ERROR: {problem}")
        else:
            argv = ["python", str(paths.run_sample),
                    "--sample", name,
                    "--bam", str(links_dir),
                    "--out_dir", str(sample_out),
                    "--reference", str(paths.reference),
                    "--manifest", str(paths.manifest),
                    "--model_dir", str(paths.model_dir)]
            argv += ["--panel", str(paths.panel)] if paths.panel else ["--no_panel"]
            if sample.sex.strip():
                argv += ["--sex", sample.sex.strip()]
            blast = sample.blast_percent.strip() or str(cfg.get("blast_percent", "")).strip()
            if blast:
                argv += ["--blast_percent", blast]
            threads = str(cfg.get("threads", "")).strip()
            if threads.isdigit() and int(threads) > 0:
                argv += ["--threads", threads]
            aligner = str(cfg.get("aligner", "")).strip().lower()
            if aligner in ("dorado", "minimap2"):
                argv += ["--aligner", aligner]
            argv += ["--device", _device(cfg)]
            argv += extra_args(cfg)
            try:
                if not bams:
                    raise OSError("no complete BAM files to analyse")
                it.iter_dir.mkdir(parents=True, exist_ok=True)
                make_bam_links(bams, links_dir)
            except OSError as exc:
                log(f"ERROR: could not prepare the BAM links folder {links_dir}: {exc}")
            else:
                span = Path(bams[0]).name + (f" … {Path(bams[-1]).name}" if len(bams) > 1 else "")
                where = (f"Iteration {it.number}: BAM links folder {links_dir} "
                         f"({len(bams)} BAM file(s): {span})")
                log(where)
                _announce(f"{name}: {where}")
                try:
                    with open(log_file, "a") as fh:
                        fh.write(where + "\n")
                except OSError:
                    pass
                script = _build_script(cfg, argv, log_file, paths.sparsh)
                log(f"--- Iteration {it.number}: run_sample.py on {len(bams)} BAM file(s) ---")
                log("CMD: " + script)
                ok = self._execute(script, epoch, [log], "run_sample.py")
        if self._epoch != epoch:
            return                               # paused: stop() re-queued this sample

        if ok:
            self._set_step(it, epoch, "collecting files")
            n_csv = self._collect(it, name, sample_out / "methylation",
                                  lambda p: p.suffix.lower() == ".csv",
                                  it.iter_dir / "csv", log)
            calls_dir = sample_out / "copy_number" / "calls"
            prefix = name.upper() + "."        # the copy-number module upper-cases names
            n_kar = self._collect(it, name, calls_dir,
                                  lambda p: p.name.upper().startswith(prefix),
                                  it.iter_dir / "karyotype", log)
            if n_csv == 0:
                log(f"WARNING: no methylation CSV found in {sample_out / 'methylation'}; "
                    "this sample will be missing from predict.py")
            if n_kar == 0 and calls_dir.is_dir():
                log(f"WARNING: no {name}.* files in {calls_dir}; the report will have no "
                    "karyotype for this sample")
            log(f"Collected {n_csv} file(s) into csv/ and {n_kar} into karyotype/")
            lts_dir = self._lts_iter_dir(cfg, it)
            if lts_dir is not None and self._set_step(it, epoch, "copying to long-term storage"):
                try:
                    copied, left_out = mirror_without_bams(sample_out, lts_dir / name)
                    copy_file(log_file, lts_dir / log_file.name)
                    log(f"Long-term storage: {copied} file(s) copied, {left_out} BAM/index "
                        f"file(s) left out -> {lts_dir / name}")
                except OSError as exc:
                    log(f"WARNING: long-term storage copy failed ({exc}); "
                        "retried at the end of the iteration")

        with self._cv:
            if self._epoch != epoch:
                return
            it.running = None
            (it.ok if ok else it.failed).append(name)
            app_state.record_attempt(name, len(bams), ok, it.number)
            log(f"Iteration {it.number}: {name} finished" if ok else
                f"Iteration {it.number}: {name} FAILED — see {log_file}")
            self._publish()

    def _collect(self, it: _Iteration, name: str, src: Path, wanted: Callable[[Path], bool],
                 dest: Path, log: Callable[[str], None]) -> int:
        """Copy the items of src that `wanted` accepts into dest. Returns how many."""
        dest.mkdir(parents=True, exist_ok=True)
        if not src.is_dir():
            log(f"WARNING: {src} not found — nothing copied into {dest.name}/")
            return 0
        count = 0
        for item in sorted(p for p in src.iterdir() if wanted(p)):
            target = dest / item.name
            owner = it.collected.get(str(target))
            if owner and owner != name:
                log(f"WARNING: {dest.name}/{item.name} from {owner} overwritten by {name}")
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            elif item.is_file():
                shutil.copy2(item, target)
            else:
                continue
            it.collected[str(target)] = name
            count += 1
        return count

    # ── once per iteration ────────────────────────────────────────

    def _run_aggregate(self, it: _Iteration, cfg: dict, epoch: int):
        members = [n for n in it.members if app_state.get_sample(n)]

        def log_all(msg: str):
            for n in members:
                app_state.add_log(n, msg)

        if not it.ok and not it.failed:          # every member was removed
            with self._cv:
                if self._epoch == epoch and self._iter is it:
                    self._iter = None
                    self._schedule()
                    self._publish()
                    self._cv.notify_all()
            return

        paths = resolve(cfg)
        d = it.iter_dir
        agg_log = d / "predict_report.log"
        predict_ok: Optional[bool] = None
        report_ok: Optional[bool] = None

        if it.ok and not paths.ok:
            for problem in paths.problems:
                log_all(f"ERROR: {problem}")
            predict_ok = False
        elif it.ok:
            for stale in (d / "predictions", d / "report"):    # left by a paused attempt
                if stale.exists():
                    shutil.rmtree(stale, ignore_errors=True)
            (d / "csv").mkdir(parents=True, exist_ok=True)
            (d / "karyotype").mkdir(parents=True, exist_ok=True)

            argv = ["python", str(paths.predict),
                    "--model_dir", str(paths.model_dir),
                    "--ont_dir", str(d / "csv"),
                    "--output_dir", str(d / "predictions"),
                    "--device", _device(cfg)]
            script = _build_script(cfg, argv, agg_log, paths.sparsh)
            if not self._set_step(it, epoch, "predict.py"):
                return
            log_all(f"--- Iteration {it.number}: predict.py on {len(it.ok)} sample(s) ---")
            log_all("CMD: " + script)
            predict_ok = self._execute(script, epoch, [log_all], "predict.py")
            if self._epoch != epoch:
                return

            if predict_ok:
                argv = ["python", str(paths.report),
                        "--predictions_dir", str(d / "predictions"),
                        "--karyotype_dir", str(d / "karyotype"),
                        "--output_dir", str(d / "report"),
                        "--title", f"SPARSH report: {it.out_name}, iteration {it.number}"]
                sheet = self._write_sample_sheet(it, d, log_all)
                if sheet is not None:
                    argv += ["--sample_sheet", str(sheet)]
                script = _build_script(cfg, argv, agg_log, paths.sparsh)
                if not self._set_step(it, epoch, "report.py"):
                    return
                log_all(f"--- Iteration {it.number}: report.py ---")
                log_all("CMD: " + script)
                report_ok = self._execute(script, epoch, [log_all], "report.py")
                if self._epoch != epoch:
                    return
                if report_ok:
                    if not self._set_step(it, epoch, "moving report files"):
                        return
                    self._move_report_up(it, d, log_all)
        else:
            log_all(f"Iteration {it.number}: no sample succeeded — "
                    "predict.py and report.py skipped")

        lts_ok: Optional[bool] = None
        lts_dir = self._lts_iter_dir(cfg, it)
        if lts_dir is not None:
            if not self._set_step(it, epoch, "copying iteration to long-term storage"):
                return
            try:
                copied, left_out = mirror_without_bams(d, lts_dir)
                lts_ok = True
                log_all(f"Iteration {it.number} mirrored to long-term storage: {copied} file(s) "
                        f"copied, {left_out} BAM/index file(s) and links left out -> {lts_dir}")
            except OSError as exc:
                lts_ok = False
                log_all(f"WARNING: long-term storage copy of iteration {it.number} failed — {exc}")

        with self._cv:
            if self._epoch != epoch or self._iter is not it:
                return
            app_state.add_round_summary({
                "number": it.number, "iter_dir": str(d),
                "links_dir": str(d / "bam_links"),
                "lts_dir": str(lts_dir) if lts_dir else "",
                "ok": list(it.ok), "failed": list(it.failed),
                "predict_ok": predict_ok, "report_ok": report_ok, "lts_ok": lts_ok,
                "finished": time.time(),
            })
            log_all(f"=== Iteration {it.number} complete ===")
            self._iter = None
            self._schedule()                     # next BAM set may already be complete
            self._publish()
            self._cv.notify_all()

    def _write_sample_sheet(self, it: _Iteration, d: Path,
                            log_all: Callable[[str], None]) -> Optional[Path]:
        """
        sample_sheet.csv (sample, sex, blast_percent) for report.py, listing only the
        samples that predict.py wrote to predictions.csv: report.py refuses a sheet
        that names a sample without a prediction.
        """
        try:
            with open(d / "predictions" / "predictions.csv", newline="") as fh:
                predicted = {row.get("sample", "").strip().upper() for row in csv.DictReader(fh)}
        except (OSError, csv.Error) as exc:
            log_all(f"WARNING: predictions.csv could not be read ({exc}); "
                    "the report is made without the sample sheet")
            return None
        rows = []
        for name in it.ok:
            s = app_state.get_sample(name)
            if s is not None and name.strip().upper() in predicted:
                rows.append((name, s.sex.strip(), s.blast_percent.strip()))
        if not rows:
            return None
        sheet = d / SAMPLE_SHEET
        try:
            with open(sheet, "w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["sample", "sex", "blast_percent"])
                w.writerows(rows)
        except OSError as exc:
            log_all(f"WARNING: {sheet} could not be written ({exc}); "
                    "the report is made without the sample sheet")
            return None
        return sheet

    def _move_report_up(self, it: _Iteration, d: Path, log_all: Callable[[str], None]):
        report_dir = d / "report"
        if not report_dir.is_dir():
            log_all(f"WARNING: report.py finished but did not create {report_dir}")
            return
        protected = {"csv", "karyotype", "predictions", "report", "bam_links",
                     "predict_report.log", SAMPLE_SHEET}
        protected |= set(it.members) | {f"{m}.log" for m in it.members}
        try:
            moved, kept = move_contents_up(report_dir, d, protected)
        except OSError as exc:
            log_all(f"WARNING: could not move the report files out of {report_dir}: {exc}")
            return
        if moved == 0 and not kept:
            log_all("WARNING: report.py finished but report/ was empty")
            return
        log_all(f"Moved {moved} item(s) from report/ into {d}")
        if kept:
            log_all("WARNING: left in report/ because the name is already used in the "
                    f"iteration folder: {', '.join(kept)}")

    # ── plumbing ──────────────────────────────────────────────────

    def _execute(self, script: str, epoch: int,
                 log_fns: List[Callable[[str], None]], label: str) -> bool:
        """Run a bash script in its own process group, streaming output to log_fns."""
        if not self.wait_for_jobs():             # a job killed by Stop must be gone first
            for fn in log_fns:
                fn("WARNING: a stopped job is still ending; waiting for it")
            self.wait_for_jobs(timeout=60)
        with self._cv:
            if self._epoch != epoch:
                return False
        try:
            proc = subprocess.Popen(
                ["bash", "-c", script],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, errors="replace", bufsize=1,
                start_new_session=True, env=subprocess_env())
        except OSError as exc:
            for fn in log_fns:
                fn(f"ERROR: could not start {label}: {exc}")
            return False

        with self._cv:
            current = self._epoch == epoch
            if current:
                self._proc = proc
                app_state.set_command(script)
        if not current:                          # stopped while launching
            _terminate_group(proc).join()
            return False

        try:
            for line in proc.stdout:
                line = line.rstrip()
                if line:
                    for fn in log_fns:
                        fn(line)
        finally:
            proc.wait()
            with self._cv:
                if self._proc is proc:
                    self._proc = None
                if self._epoch == epoch:
                    app_state.set_command(None)

        if self._epoch != epoch:
            return False
        if proc.returncode != 0:
            for fn in log_fns:
                fn(f"{label} failed (exit code {proc.returncode})")
            return False
        return True

    def _set_step(self, it: _Iteration, epoch: int, step: str) -> bool:
        with self._cv:
            if self._epoch != epoch:
                return False
            it.step, it.step_since = step, time.time()
            self._publish()
            return True

    def _lts_iter_dir(self, cfg: dict, it: _Iteration) -> Optional[Path]:
        lts = cfg.get("lts_output", "").strip()
        return Path(expand(lts)) / it.out_name / str(it.number) if lts else None

    def _log_members(self, it: _Iteration, msg: str):
        for n in it.members:
            app_state.add_log(n, msg)

    def _publish(self):
        """Snapshot for the GUI (call with the lock held)."""
        it = self._iter
        if it is None:
            app_state.set_activity({"iteration": None,
                                    "wait": dict(self._wait) if self._wait else None}
                                   if self._accepting else None)
            return
        app_state.set_activity({
            "iteration": it.number, "phase": it.phase, "step": it.step,
            "since": it.step_since,
            "running": it.running, "pending": list(it.pending),
            "ok": list(it.ok), "failed": list(it.failed),
            "iter_dir": str(it.iter_dir), "links_dir": str(it.iter_dir / "bam_links"),
        })

    def _handle_unexpected(self, it: _Iteration, job: Optional[str], epoch: int,
                           exc: Exception):
        with self._cv:
            if self._epoch != epoch:
                return
            if job is not None:
                app_state.add_log(job, f"UNEXPECTED ERROR: {exc}")
                if it.running == job:
                    it.running = None
                if job not in it.failed:
                    it.failed.append(job)
                sample = app_state.get_sample(job)
                if sample is not None:
                    app_state.record_attempt(job, sample.link_count(it.number), False, it.number)
            else:
                self._log_members(it, f"UNEXPECTED ERROR in iteration {it.number}: {exc}")
                if self._iter is it:
                    self._iter = None
                    self._schedule()
            self._publish()
            self._cv.notify_all()


runner = Runner()
