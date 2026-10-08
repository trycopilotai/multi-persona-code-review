#!/usr/bin/env python3
"""Run one review command with hard and idle timeouts.

The multi-persona-code-review skill uses this helper to
make external agent passes auditable. A pass that times
out, stalls, or fails still leaves a result artifact
instead of silently disappearing from the review record,
unless the runner itself is stopped or a write fails.

Exit status: 0 when the command completed, 2 for any other
status, for invalid arguments, or for result paths the
pre-run check refuses. A result write that fails after the
command ran ends the script with a traceback and exit 1.
A command that cannot be
started raises out of ``subprocess.Popen``: the script then
exits 1 with a traceback and writes no artifact.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import selectors
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import unicodedata
from pathlib import Path
from typing import Any


# The longest the loop waits before it looks at the clock again.
POLL_SECONDS = 0.5
# The most output read in one wake, and in the last read once
# the exit is seen or the lane is stopped, so a lane or a
# descendant that writes without pause cannot keep the runner
# reading.
READ_LIMIT_BYTES = 1 << 20

FINAL_STATUSES = {
    "completed",
    "failed",
    "timed_out",
    "stalled",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a bounded code-review agent command."
    )
    parser.add_argument(
        "--name",
        required=True,
        help="Stable persona or lane name for the result artifact.",
    )
    parser.add_argument(
        "--cwd",
        default=".",
        help="Working directory for the command.",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="Markdown result file to write.",
    )
    parser.add_argument(
        "--json-out",
        help="Optional JSON result file to write.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=240.0,
        help="Hard wall-clock timeout.",
    )
    parser.add_argument(
        "--idle-seconds",
        type=float,
        default=60.0,
        help="Maximum seconds without stdout/stderr output.",
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Command to run after --.",
    )
    args = parser.parse_args()

    if args.command and args.command[0] == "--":
        args.command = args.command[1:]

    if not args.command:
        parser.error("a command is required after --")

    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")

    if args.idle_seconds <= 0:
        parser.error("--idle-seconds must be positive")

    if not math.isfinite(args.timeout_seconds):
        parser.error("--timeout-seconds must be finite")

    if not math.isfinite(args.idle_seconds):
        parser.error("--idle-seconds must be finite")

    return args


def terminate_process(process: subprocess.Popen[bytes]) -> None:
    # macOS can answer EPERM instead of ESRCH when the group's
    # leader is a zombie, so PermissionError is treated as gone.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return
        time.sleep(0.1)

    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        return


def read_available(fd: int, limit: int | None = None) -> str:
    chunks: list[bytes] = []
    total = 0

    while True:
        size = 65536
        if limit is not None:
            size = min(size, limit - total)
        try:
            data = os.read(fd, size)
        except BlockingIOError:
            break

        if not data:
            break

        chunks.append(data)
        total += len(data)
        if limit is not None and total >= limit:
            break

    return b"".join(chunks).decode("utf-8", errors="replace")


def collect_output(
    process: subprocess.Popen[bytes],
    timeout_seconds: float,
    idle_seconds: float,
) -> tuple[str, str, float, int | None]:
    if process.stdout is None:
        raise RuntimeError("process stdout was not captured")

    fd = process.stdout.fileno()
    os.set_blocking(fd, False)

    selector = selectors.DefaultSelector()
    selector.register(fd, selectors.EVENT_READ)

    started_at = time.monotonic()
    last_output_at = started_at
    output_parts: list[str] = []
    status = "running"

    while True:
        now = time.monotonic()

        if process.poll() is not None:
            # The moment the exit is seen, and the output seen before
            # it, decide the status. A deadline that had passed by
            # then makes a late exit timed_out or stalled; the last
            # read below does not change the decision.
            seen_at = time.monotonic()
            status = "completed"
            if seen_at - started_at > timeout_seconds:
                status = "timed_out"
            elif seen_at - last_output_at > idle_seconds:
                status = "stalled"
            output = read_available(fd, READ_LIMIT_BYTES)
            if output:
                output_parts.append(output)
            break

        if now - started_at > timeout_seconds:
            status = "timed_out"
            terminate_process(process)
            output = read_available(fd, READ_LIMIT_BYTES)
            if output:
                output_parts.append(output)
            break

        if now - last_output_at > idle_seconds:
            status = "stalled"
            terminate_process(process)
            output = read_available(fd, READ_LIMIT_BYTES)
            if output:
                output_parts.append(output)
            break

        wait = min(
            POLL_SECONDS,
            started_at + timeout_seconds - now,
            last_output_at + idle_seconds - now,
        )
        events = selector.select(timeout=max(wait, 0.0))
        for key, _mask in events:
            output = read_available(key.fd, READ_LIMIT_BYTES)
            if output:
                output_parts.append(output)
                last_output_at = time.monotonic()

    duration_seconds = time.monotonic() - started_at
    return_code = process.wait()

    if status == "completed" and return_code != 0:
        status = "failed"

    if status not in FINAL_STATUSES:
        status = "failed"

    return status, "".join(output_parts), duration_seconds, return_code


def make_markdown(result: dict[str, Any]) -> str:
    command = result["command"]
    output = result["output"]

    return "\n".join(
        [
            "# Bounded Code Review Result",
            "",
            f"- name: `{result['name']}`",
            f"- status: `{result['status']}`",
            f"- cwd: `{result['cwd']}`",
            f"- duration_seconds: `{result['duration_seconds']:.2f}`",
            f"- timeout_seconds: `{result['timeout_seconds']:.2f}`",
            f"- idle_seconds: `{result['idle_seconds']:.2f}`",
            f"- return_code: `{result['return_code']}`",
            "",
            "## Command",
            "",
            "```sh",
            shlex.join(command),
            "```",
            "",
            "## Output",
            "",
            "```text",
            output.rstrip(),
            "```",
            "",
        ]
    )


def write_result_files(
    result: dict[str, Any],
    markdown_path: Path,
    json_path: Path | None,
) -> None:
    """Write each file beside its target, then rename both into place.

    If a write fails, the temporary files are removed and neither
    target is created or changed. Only a failure of the second
    rename, after the first succeeded, leaves one file in place.
    """
    outputs = [(markdown_path, make_markdown(result))]
    if json_path is not None:
        outputs.append(
            (json_path, json.dumps(result, indent=2, sort_keys=True) + "\n")
        )

    staged: list[tuple[str, Path]] = []
    try:
        for path, text in outputs:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle, temporary = tempfile.mkstemp(
                dir=str(path.parent), prefix=".rbr-", suffix=".tmp"
            )
            staged.append((temporary, path))
            with os.fdopen(
                handle, "w", encoding="utf-8", errors="backslashreplace"
            ) as stream:
                stream.write(text)
            os.chmod(temporary, 0o666 & ~current_umask())
        for temporary, path in staged:
            os.replace(temporary, path)
    except BaseException:
        for temporary, _path in staged:
            if os.path.exists(temporary):
                os.unlink(temporary)
        raise


def current_umask() -> int:
    mask = os.umask(0)
    os.umask(mask)
    return mask


class ResultPathError(Exception):
    """A result path the runner refuses before the command runs."""


def normalised(name: str) -> str:
    return unicodedata.normalize("NFC", name).casefold()


def names_overlap(first: Path, second: Path) -> bool:
    """Whether one path equals or contains the other, by name.

    Names are compared after Unicode NFC normalisation and case folding,
    so a file system that ignores case or normalisation cannot make the
    second rename replace the first.
    """
    first_parts = [normalised(part) for part in first.parts]
    second_parts = [normalised(part) for part in second.parts]
    shorter = min(len(first_parts), len(second_parts))
    return first_parts[:shorter] == second_parts[:shorter]


def same_entry(first: Path, second: Path) -> bool:
    """Whether two result paths name one directory entry, by identity.

    Existing files are compared by device and inode, and existing parent
    directories by device and inode plus the normalised file name.
    """
    if first.exists() and second.exists():
        if os.path.samefile(str(first), str(second)):
            return True
    if first.parent.is_dir() and second.parent.is_dir():
        if os.path.samefile(str(first.parent), str(second.parent)):
            if normalised(first.name) == normalised(second.name):
                return True
    return False


def check_writable(path: Path) -> None:
    """Refuse a result path that cannot be written, before the command runs."""
    try:
        os.lstat(str(path))
    except FileNotFoundError:
        pass
    if path.is_dir():
        raise ResultPathError("cannot write %s: it is a directory" % path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        dir=str(path.parent), prefix=".rbr-", suffix=".tmp"
    )
    os.close(handle)
    os.unlink(temporary)


def validate_result_paths(
    out: str, json_out: str | None
) -> tuple[Path, Path | None]:
    """Resolve and check both result paths before the command runs.

    Every failure in this phase, including an OSError or RuntimeError
    from the file system, becomes a ResultPathError, which main reports
    with exit status 2.
    """
    try:
        out_path = Path(out).resolve()
        json_path = None
        if json_out:
            json_path = Path(json_out).resolve()
        paths = [out_path]
        if json_path is not None:
            if names_overlap(out_path, json_path):
                raise ResultPathError("--out and --json-out must be separate files")
            paths.append(json_path)
        for path in paths:
            check_writable(path)
        if json_path is not None and same_entry(out_path, json_path):
            raise ResultPathError("--out and --json-out must be separate files")
    except ResultPathError:
        raise
    except (OSError, RuntimeError) as error:
        raise ResultPathError("cannot write the result files: %s" % error)
    return out_path, json_path


def main() -> int:
    args = parse_args()
    cwd = Path(args.cwd).resolve()
    try:
        out_path, json_path = validate_result_paths(args.out, args.json_out)
    except ResultPathError as error:
        sys.stderr.write("run_bounded_review.py: error: %s\n" % error)
        return 2

    process = subprocess.Popen(
        args.command,
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        preexec_fn=os.setsid,
    )

    status, output, duration_seconds, return_code = collect_output(
        process,
        args.timeout_seconds,
        args.idle_seconds,
    )

    result: dict[str, Any] = {
        "name": args.name,
        "status": status,
        "cwd": str(cwd),
        "command": args.command,
        "duration_seconds": duration_seconds,
        "timeout_seconds": args.timeout_seconds,
        "idle_seconds": args.idle_seconds,
        "return_code": return_code,
        "output": output,
    }
    write_result_files(result, out_path, json_path)

    if status == "completed":
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
