#!/usr/bin/env python3
"""Record the bounded-lanes session again and refresh the evidence manifest.

    python3 scripts/record_session.py

The commands are the ones listed in ``evidence/demo-manifest.json``.
They run in a throwaway directory that holds a copy of ``skills/``, an
empty ``out/`` directory, the two stand-in agent scripts below under
``agents/``, and a git repository named ``repo`` with one commit and one
uncommitted change to ``greet.py``. No coding agent runs: each lane's
command is a shell command or one of the two stand-in scripts, so the
session shows what the runner records for each way a lane can end.

The transcript is what a shell would show: each command line, the
output, and the exit status. One edit is made before it is written: the
throwaway directory's absolute path is replaced with ``/work``.

The manifest's hashes of ``SKILL.md``, the program and the transcript
are then rewritten, with the date and the interpreter. Run ``make demo``
afterwards to rebuild the images.

Set ``RECORD_RAW_DIR`` to also keep the unedited capture and the
replaced path, outside the repository.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "evidence" / "demo-manifest.json"
PLACEHOLDER = "/work"

COMMITTED = 'def greet(name):\n    return "Hello, " + name\n'
CHANGED = 'def greet(name):\n    return "Hello, " + name[0].upper() + name[1:]\n'

# A stand-in for a persona agent. It prints one fixed finding about the
# uncommitted change, in the persona output shape SKILL.md requires.
FINDING = """#!/bin/sh
git diff --quiet && { echo "status: no_findings"; exit 0; }
cat <<'OUT'
status: findings
findings:
- severity: P2
  file: greet.py
  line: 2
  issue: greet("") raises IndexError on an empty name.
  impact: An empty name crashes the caller instead of greeting.
  recommendation: Use name[:1].upper() + name[1:].
  test: Call greet("") and expect "Hello, ".
reviewed_files:
- greet.py
commands_run:
- git diff --quiet
confidence: high
OUT
"""

# A stand-in for an agent that keeps printing and never finishes.
ENDLESS = """#!/bin/sh
while :; do
  echo "still reading the diff"
  sleep 0.25
done
"""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(workdir: Path, environment: dict) -> None:
    """The stand-in agents, the output directory, and the repository."""
    shutil.copytree(
        ROOT / "skills",
        workdir / "skills",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    for name in ("agents", "out", "tmp", "home"):
        (workdir / name).mkdir()
    (workdir / "agents" / "finding.sh").write_text(FINDING, encoding="utf-8")
    (workdir / "agents" / "endless.sh").write_text(ENDLESS, encoding="utf-8")
    repo = workdir / "repo"
    repo.mkdir()
    (repo / "greet.py").write_text(COMMITTED, encoding="utf-8")
    for arguments in (
        ["init", "--quiet", str(repo)],
        ["-C", str(repo), "add", "greet.py"],
        ["-C", str(repo), "commit", "--quiet", "-m", "Add greet"],
    ):
        subprocess.run(["git", *arguments], check=True, env=environment)
    (repo / "greet.py").write_text(CHANGED, encoding="utf-8")


def record(commands: list[str], workdir: Path, environment: dict) -> str:
    define, steps = commands[0], commands[1:]
    lines = ["$ " + define]
    for step in steps:
        result = subprocess.run(
            ["bash", "-c", define + "\n" + step],
            cwd=workdir,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        lines.append("$ " + step)
        lines.extend(result.stdout.splitlines())
        lines.append('$ echo "exit status: $?"')
        lines.append("exit status: %d" % result.returncode)
    return "\n".join(lines) + "\n"


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    commands = manifest["invocation"]["commands"]
    with tempfile.TemporaryDirectory() as scratch:
        workdir = Path(scratch).resolve() / "capture"
        bindir = workdir / "bin"
        bindir.mkdir(parents=True)
        (bindir / "python3").symlink_to(sys.executable)
        environment = {
            "PATH": "%s:/usr/bin:/bin" % bindir,
            "PYTHONDONTWRITEBYTECODE": "1",
            "TMPDIR": str(workdir / "tmp"),
            "HOME": str(workdir / "home"),
            "LC_ALL": "C",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Example",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Example",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        }
        prepare(workdir, environment)
        raw = record(commands, workdir, environment)
        capture_root = str(workdir)

    raw_dir = os.environ.get("RECORD_RAW_DIR")
    if raw_dir:
        Path(raw_dir, "bounded-session.source.txt").write_text(raw, encoding="utf-8")
        Path(raw_dir, "bounded-session.capture-root.txt").write_text(
            capture_root + "\n", encoding="utf-8"
        )

    transcript = ROOT / manifest["output"]["path"]
    transcript.write_text(raw.replace(capture_root, PLACEHOLDER), encoding="utf-8")

    manifest["date"] = datetime.date.today().isoformat()
    manifest["invocation"]["interpreter"] = "Python " + platform.python_version()
    manifest["skill"]["sha256"] = sha256(ROOT / manifest["skill"]["path"])
    for program in manifest["programs"]:
        program["sha256"] = sha256(ROOT / program["path"])
    manifest["output"]["sha256"] = sha256(transcript)
    MANIFEST.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print("wrote %s" % transcript.relative_to(ROOT))
    print("wrote %s" % MANIFEST.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
