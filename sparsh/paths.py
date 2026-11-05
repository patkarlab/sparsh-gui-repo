"""
paths.py — finds the SPARSH files the commands need, from the settings.

The SPARSH folder is the repository folder (scripts/ inside it). The reference FASTA,
the EPIC manifest and the model folder can be given explicitly; when their boxes are
blank they are looked for inside the SPARSH folder under their usual names.
"""
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

SCRIPTS = {
    "run_sample": "scripts/run_sample.py",
    "predict":    "scripts/predict.py",
    "report":     "scripts/report.py",
}
REFERENCE_NAMES = ("hg19_all.fasta", "hg19_main.fa", "hg19.fa", "hg19.fasta")
MANIFEST_NAME = "EPIC-8v2-0_A2.csv"
MODEL_GLOB = "sparsh_model_*"


def expand(path: str) -> str:
    return os.path.expanduser(path.strip()) if path else ""


@dataclass
class SparshPaths:
    sparsh:     Optional[Path] = None
    run_sample: Optional[Path] = None
    predict:    Optional[Path] = None
    report:     Optional[Path] = None
    reference:  Optional[Path] = None
    manifest:   Optional[Path] = None
    model_dir:  Optional[Path] = None
    panel:      Optional[Path] = None
    problems:   List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def summary(self) -> str:
        """One line per file, for the Check setup dialog."""
        lines = []
        for label, value in (("SPARSH folder", self.sparsh), ("run_sample.py", self.run_sample),
                             ("predict.py", self.predict), ("report.py", self.report),
                             ("Reference FASTA", self.reference), ("EPIC manifest", self.manifest),
                             ("Model folder", self.model_dir), ("Copy-number panel", self.panel)):
            if value is not None:
                lines.append(f"{label}: {value}")
        return "\n".join(lines)


def _only(folder: Path, patterns, folders: bool = False) -> Optional[Path]:
    """The single file (or folder, with folders=True) in `folder` matching any of the
    patterns, else None: with two candidates the choice is left to the user."""
    try:
        hits = sorted({p for pat in patterns for p in folder.glob(pat)
                       if not p.name.startswith(".") and (p.is_dir() if folders else p.is_file())})
    except OSError:
        return None
    return hits[0] if len(hits) == 1 else None


def resolve(cfg: dict) -> SparshPaths:
    r = SparshPaths()
    loc = expand(cfg.get("sparsh_location", ""))
    if not loc:
        r.problems.append("SPARSH folder is not set (Settings).")
    elif not Path(loc).is_dir():
        r.problems.append(f"SPARSH folder not found: {loc}")
    else:
        r.sparsh = Path(loc)
        for key, rel in SCRIPTS.items():
            script = r.sparsh / rel
            if script.is_file():
                setattr(r, key, script)
            else:
                r.problems.append(f"Missing in the SPARSH folder: {rel}")

    refs = r.sparsh / "references" if r.sparsh else None

    given = expand(cfg.get("reference_fasta", ""))
    if given:
        r.reference = Path(given)
    elif refs is not None:
        r.reference = next((refs / n for n in REFERENCE_NAMES if (refs / n).is_file()), None) \
            or _only(refs, ("*.fa", "*.fasta", "*.fna"))
    if r.reference is None:
        r.problems.append("Reference FASTA not found: set it in Settings, or put it in "
                          "<SPARSH folder>/references/.")
    elif not r.reference.is_file():
        r.problems.append(f"Reference FASTA not found: {r.reference}")
    elif not Path(str(r.reference) + ".fai").is_file():
        r.problems.append(f"The reference has no .fai index: run  samtools faidx {r.reference}")

    given = expand(cfg.get("epic_manifest", ""))
    if given:
        r.manifest = Path(given)
    elif refs is not None:
        r.manifest = (refs / MANIFEST_NAME) if (refs / MANIFEST_NAME).is_file() \
            else _only(refs, ("EPIC*.csv", "epic*.csv"))
    if r.manifest is None:
        r.problems.append("EPIC manifest not found: set it in Settings, or put "
                          f"{MANIFEST_NAME} in <SPARSH folder>/references/.")
    elif not r.manifest.is_file():
        r.problems.append(f"EPIC manifest not found: {r.manifest}")

    given = expand(cfg.get("model_dir", ""))
    if given:
        r.model_dir = Path(given)
    elif r.sparsh is not None:
        r.model_dir = _only(r.sparsh, (MODEL_GLOB,), folders=True)
    if r.model_dir is None:
        r.problems.append("Model folder not found: set it in Settings, or put the "
                          "sparsh_model_* folder inside the SPARSH folder.")
    elif not r.model_dir.is_dir():
        r.problems.append(f"Model folder not found: {r.model_dir}")

    panel = expand(cfg.get("panel", ""))
    if panel:
        r.panel = Path(panel)
        if not r.panel.is_file():
            r.problems.append(f"Copy-number panel file not found: {panel}")
    return r


def conda_env_dirs(conda_sh: str, env: str) -> List[Path]:
    """Where an environment of that name would be, for a warning when none of them exists."""
    env = env.strip()
    if not env:
        return []
    if "/" in env:
        return [Path(expand(env))]
    dirs = [Path.home() / ".conda" / "envs" / env]
    sh = Path(expand(conda_sh))
    if len(sh.parents) >= 3:                    # <root>/etc/profile.d/conda.sh
        dirs.insert(0, sh.parents[2] / "envs" / env)
    for extra in os.environ.get("CONDA_ENVS_PATH", "").split(os.pathsep):
        if extra:
            dirs.append(Path(expand(extra)) / env)
    return dirs
