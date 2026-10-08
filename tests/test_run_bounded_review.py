#!/usr/bin/env python3
"""Tests for skills/multi-persona-code-review/scripts/run_bounded_review.py.

Most tests run the program as a subprocess, the way SKILL.md
tells an agent to, with a small Python command as the lane.
A few call its reader or its collection loop directly, for
cases a real command cannot hit on demand. They need a POSIX
system, because the program starts each lane in its own
process group.

    python3 tests/test_run_bounded_review.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
PROGRAM = ROOT / "skills" / "multi-persona-code-review" / "scripts" / "run_bounded_review.py"
RESULT_KEYS = [
    "command",
    "cwd",
    "duration_seconds",
    "idle_seconds",
    "name",
    "output",
    "return_code",
    "status",
    "timeout_seconds",
]


def python_command(source: str) -> list:
    return [sys.executable, "-c", source]


class RunnerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._scratch = tempfile.TemporaryDirectory()
        self.scratch = Path(self._scratch.name).resolve()
        self.addCleanup(self._scratch.cleanup)

    def run_lane(self, command: list, *options: str, json_out: bool = True):
        """Run the program; return its process result and the parsed JSON."""
        markdown = self.scratch / "out" / "lane.md"
        arguments = [
            sys.executable,
            str(PROGRAM),
            "--name",
            "lane",
            "--cwd",
            str(self.scratch),
            "--out",
            str(markdown),
        ]
        record = None
        if json_out:
            arguments += ["--json-out", str(self.scratch / "out" / "lane.json")]
        arguments += list(options)
        arguments += ["--", *command]
        result = subprocess.run(arguments, capture_output=True, text=True, timeout=60)
        if json_out and (self.scratch / "out" / "lane.json").exists():
            record = json.loads((self.scratch / "out" / "lane.json").read_text())
        return result, record

    def markdown(self) -> str:
        return (self.scratch / "out" / "lane.md").read_text(encoding="utf-8")


class StatusTest(RunnerTestCase):
    def test_a_command_that_exits_0_is_completed_and_the_runner_exits_0(self) -> None:
        result, record = self.run_lane(
            python_command(
                "import sys; print('to stdout'); print('to stderr', file=sys.stderr)"
            )
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sorted(record), RESULT_KEYS)
        self.assertEqual(record["status"], "completed")
        self.assertEqual(record["return_code"], 0)
        self.assertIn("to stdout", record["output"])
        self.assertIn("to stderr", record["output"])
        self.assertEqual(record["name"], "lane")
        self.assertIn("- status: `completed`", self.markdown())

    def test_a_command_that_exits_non_zero_is_failed_and_the_runner_exits_2(
        self,
    ) -> None:
        result, record = self.run_lane(
            python_command("import sys; print('partial'); sys.exit(3)")
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["return_code"], 3)
        self.assertIn("partial", record["output"])
        self.assertIn("- status: `failed`", self.markdown())

    def test_a_silent_command_is_stalled_after_the_idle_budget(self) -> None:
        started = time.monotonic()
        result, record = self.run_lane(
            python_command("import time; time.sleep(30)"),
            "--idle-seconds",
            "0.5",
            "--timeout-seconds",
            "20",
        )
        self.assertLess(time.monotonic() - started, 15)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(record["status"], "stalled")
        self.assertEqual(record["return_code"], -signal.SIGTERM)
        self.assertIn("- status: `stalled`", self.markdown())

    def test_a_command_that_keeps_printing_is_timed_out_at_the_hard_budget(
        self,
    ) -> None:
        started = time.monotonic()
        result, record = self.run_lane(
            python_command(
                "import time\n"
                "while True:\n"
                "    print('tick', flush=True)\n"
                "    time.sleep(0.1)\n"
            ),
            "--timeout-seconds",
            "1",
            "--idle-seconds",
            "5",
        )
        self.assertLess(time.monotonic() - started, 15)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(record["status"], "timed_out")
        self.assertIn("tick", record["output"])
        self.assertGreaterEqual(record["duration_seconds"], 1)
        self.assertIn("- status: `timed_out`", self.markdown())

    def test_a_descendant_that_keeps_writing_does_not_hold_the_runner(
        self,
    ) -> None:
        flood = (
            "import os, time\n"
            "end = time.time() + 4\n"
            "while time.time() < end:\n"
            "    os.write(1, b'y' * 65536)\n"
        )
        leader = (
            "import subprocess, sys\n"
            "subprocess.Popen([sys.executable, '-c', %r])\n" % flood
        )
        started = time.monotonic()
        result, record = self.run_lane(python_command(leader))
        self.assertLess(time.monotonic() - started, 3.5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(record["status"], "completed")
        # How much is read before the exit is seen depends on scheduling,
        # so the bound on what is read after it is checked in
        # PostExitReadTest, where the exit is seen at once.

    def test_a_command_that_exits_after_the_hard_budget_is_not_completed(
        self,
    ) -> None:
        _, record = self.run_lane(
            python_command("import time; time.sleep(0.4)"),
            "--timeout-seconds",
            "0.1",
        )
        self.assertEqual(record["status"], "timed_out")

    def test_a_command_that_exits_after_the_idle_budget_is_not_completed(
        self,
    ) -> None:
        _, record = self.run_lane(
            python_command("import time; print('x', flush=True); time.sleep(0.4)"),
            "--idle-seconds",
            "0.1",
        )
        self.assertEqual(record["status"], "stalled")

    def test_output_before_a_stall_is_kept(self) -> None:
        _, record = self.run_lane(
            python_command("import time; print('started', flush=True); time.sleep(30)"),
            "--idle-seconds",
            "0.5",
        )
        self.assertEqual(record["status"], "stalled")
        self.assertIn("started", record["output"])


class ReadLimitTest(unittest.TestCase):
    """A lane that writes without pause must not keep the reader in one call.

    Running such a lane end to end would hold gigabytes of output in
    memory, so this calls the reader directly on a pipe that a thread
    keeps full.
    """

    def test_one_read_returns_after_about_the_limit(self) -> None:
        spec = importlib.util.spec_from_file_location("run_bounded_review", PROGRAM)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        read_end, write_end = os.pipe()
        os.set_blocking(read_end, False)
        stop = threading.Event()

        def flood() -> None:
            block = b"y" * 65536
            try:
                while not stop.is_set():
                    os.write(write_end, block)
            except OSError:
                return

        writer = threading.Thread(target=flood, daemon=True)
        writer.start()
        try:
            time.sleep(0.1)
            limit = 1 << 18
            started = time.monotonic()
            text = module.read_available(read_end, limit)
            self.assertLess(time.monotonic() - started, 5)
            self.assertGreater(len(text), 0)
            self.assertLessEqual(len(text), limit)
            self.assertEqual(module.READ_LIMIT_BYTES, 1 << 20)
        finally:
            stop.set()
            os.close(read_end)
            writer.join(timeout=5)
            os.close(write_end)


class PostExitReadTest(unittest.TestCase):
    """After the exit is seen, at most READ_LIMIT_BYTES more are read.

    A thread keeps the pipe full, as a descendant that outlives the
    command would, and the stand-in process reports its exit on the
    first poll, so every byte in the output was read after the exit was
    seen.
    """

    def test_the_last_read_stops_at_the_limit(self) -> None:
        spec = importlib.util.spec_from_file_location("run_bounded_review", PROGRAM)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        read_end, write_end = os.pipe()
        stop = threading.Event()

        def flood() -> None:
            block = b"y" * 65536
            try:
                while not stop.is_set():
                    os.write(write_end, block)
            except OSError:
                return

        class Exited:
            pid = -1

            def __init__(self, stdout) -> None:
                self.stdout = stdout

            def poll(self):
                return 0

            def wait(self) -> int:
                return 0

        writer = threading.Thread(target=flood, daemon=True)
        writer.start()
        try:
            time.sleep(0.1)
            with os.fdopen(read_end, "rb") as stdout:
                status, output, _duration, _code = module.collect_output(
                    Exited(stdout), 10, 10
                )
        finally:
            stop.set()
            writer.join(timeout=5)
            os.close(write_end)
        self.assertEqual(status, "completed")
        self.assertGreater(len(output), 0)
        self.assertLessEqual(len(output), module.READ_LIMIT_BYTES)


class LateExitTest(unittest.TestCase):
    """An exit the runner first sees after a deadline is not `completed`.

    A real command cannot be made to exit in the instant between a
    deadline and the runner's check, so this drives the collection loop
    with a stand-in process whose exit is first reported after the
    budget has passed.
    """

    class LateProcess:
        def __init__(self, stdout, delay: float, write_end: int, last_words: bytes) -> None:
            self.stdout = stdout
            self.pid = -1
            self.delay = delay
            self.calls = 0
            self.write_end = write_end
            self.last_words = last_words

        def poll(self):
            self.calls += 1
            if self.calls == 1:
                return None
            time.sleep(self.delay)
            if self.last_words:
                # Output the runner has not read yet when it sees the exit.
                os.write(self.write_end, self.last_words)
            return 0

        def wait(self) -> int:
            return 0

    def load(self):
        spec = importlib.util.spec_from_file_location("run_bounded_review", PROGRAM)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def collect(self, timeout: float, idle: float, last_words: bytes = b"", module=None):
        if module is None:
            module = self.load()
        read_end, write_end = os.pipe()
        self.addCleanup(os.close, write_end)
        with os.fdopen(read_end, "rb") as stdout:
            process = self.LateProcess(stdout, 0.3, write_end, last_words)
            return module.collect_output(process, timeout, idle)

    def test_output_read_after_the_idle_deadline_does_not_undo_a_stall(
        self,
    ) -> None:
        status, output, _duration, _code = self.collect(10, 0.1, b"late words\n")
        self.assertEqual(status, "stalled")
        self.assertIn("late words", output)

    def test_a_slow_last_read_does_not_time_out_an_exit_seen_in_time(self) -> None:
        module = self.load()
        original = module.read_available

        def slow_read(fd, limit=None):
            time.sleep(0.5)
            return original(fd, limit)

        module.read_available = slow_read
        read_end, write_end = os.pipe()
        self.addCleanup(os.close, write_end)

        class InTime:
            pid = -1

            def __init__(self, stdout) -> None:
                self.stdout = stdout

            def poll(self):
                return 0

            def wait(self) -> int:
                return 0

        with os.fdopen(read_end, "rb") as stdout:
            status, _output, duration, _code = module.collect_output(
                InTime(stdout), 0.2, 0.2
            )
        self.assertEqual(status, "completed")
        self.assertGreater(duration, 0.2)

    def test_a_hard_deadline_passed_before_the_exit_was_seen(self) -> None:
        status, _output, _duration, return_code = self.collect(0.1, 10)
        self.assertEqual(status, "timed_out")
        self.assertEqual(return_code, 0)

    def test_an_idle_deadline_passed_before_the_exit_was_seen(self) -> None:
        status, _output, _duration, _code = self.collect(10, 0.1)
        self.assertEqual(status, "stalled")

    def test_an_exit_seen_before_both_deadlines_is_completed(self) -> None:
        status, _output, _duration, _code = self.collect(10, 10)
        self.assertEqual(status, "completed")


class ArtifactTest(RunnerTestCase):
    def test_the_lane_reads_an_empty_stdin_not_the_runners(self) -> None:
        markdown = self.scratch / "lane.md"
        record = self.scratch / "lane.json"
        runner = subprocess.Popen(
            [
                sys.executable,
                str(PROGRAM),
                "--name",
                "lane",
                "--out",
                str(markdown),
                "--json-out",
                str(record),
                "--idle-seconds",
                "5",
                "--",
                *python_command("import sys; print('read %d bytes' % len(sys.stdin.read()))"),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            # The runner's own stdin stays open; a lane that inherited it
            # would wait until the idle budget ended it.
            runner.wait(timeout=4)
        finally:
            runner.stdin.close()
            if runner.poll() is None:
                runner.kill()
            runner.wait()
            runner.stdout.close()
            runner.stderr.close()
        data = json.loads(record.read_text())
        self.assertEqual(data["status"], "completed")
        self.assertIn("read 0 bytes", data["output"])

    def test_json_is_written_only_when_asked_for(self) -> None:
        result, record = self.run_lane(python_command("print('x')"), json_out=False)
        self.assertEqual(result.returncode, 0)
        self.assertIsNone(record)
        self.assertEqual(sorted(p.name for p in (self.scratch / "out").iterdir()), ["lane.md"])

    def test_missing_parent_directories_are_created(self) -> None:
        markdown = self.scratch / "a" / "b" / "lane.md"
        record = self.scratch / "c" / "d" / "lane.json"
        result = subprocess.run(
            [
                sys.executable,
                str(PROGRAM),
                "--name",
                "lane",
                "--out",
                str(markdown),
                "--json-out",
                str(record),
                "--",
                *python_command("pass"),
            ],
            capture_output=True,
            text=True,
            cwd=str(self.scratch),
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(markdown.is_file())
        self.assertTrue(record.is_file())

    def test_a_command_line_that_is_not_utf8_still_gets_both_files(self) -> None:
        markdown = self.scratch / "lane.md"
        record = self.scratch / "lane.json"
        result = subprocess.run(
            [
                sys.executable.encode(),
                str(PROGRAM).encode(),
                b"--name",
                b"lane",
                b"--out",
                str(markdown).encode(),
                b"--json-out",
                str(record).encode(),
                b"--",
                sys.executable.encode(),
                b"-c",
                b"pass",
                b"\xff",
            ],
            capture_output=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("\\udcff", markdown.read_text(encoding="utf-8"))
        self.assertEqual(json.loads(record.read_text())["status"], "completed")

    def test_the_double_dash_may_be_left_out(self) -> None:
        markdown = self.scratch / "lane.md"
        record = self.scratch / "lane.json"
        result = subprocess.run(
            [
                sys.executable,
                str(PROGRAM),
                "--name",
                "lane",
                "--out",
                str(markdown),
                "--json-out",
                str(record),
                sys.executable,
                "-c",
                "import sys; print(sys.argv[1:])",
                "--out",
                "kept",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(record.read_text())
        self.assertEqual(data["command"][1:], ["-c", "import sys; print(sys.argv[1:])", "--out", "kept"])
        self.assertIn("['--out', 'kept']", data["output"])

    def test_the_recorded_command_and_cwd(self) -> None:
        command = python_command("import os; print(os.getcwd())")
        _, record = self.run_lane(command)
        self.assertEqual(record["command"], command)
        self.assertEqual(record["cwd"], str(self.scratch))
        self.assertEqual(Path(record["output"].strip()).resolve(), self.scratch)
        self.assertEqual(record["timeout_seconds"], 240.0)
        self.assertEqual(record["idle_seconds"], 60.0)

    def test_markdown_fences_the_output(self) -> None:
        self.run_lane(python_command("print('line one'); print('line two')"))
        text = self.markdown()
        self.assertTrue(text.startswith("# Bounded Code Review Result\n"))
        self.assertIn("```text\nline one\nline two\n```", text)
        self.assertIn("## Command", text)


class WriteTest(RunnerTestCase):
    def lane(self, *arguments: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(PROGRAM), "--name", "lane", *arguments],
            capture_output=True,
            text=True,
            cwd=str(self.scratch),
            timeout=60,
        )

    def test_an_unwritable_result_path_stops_before_the_command(self) -> None:
        (self.scratch / "blocker").write_text("a file, not a directory")
        marker = self.scratch / "ran"
        result = self.lane(
            "--out",
            "lane.md",
            "--json-out",
            "blocker/lane.json",
            "--",
            *python_command("open(%r, 'w').write('x')" % str(marker)),
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("error: cannot write", result.stderr)
        self.assertFalse(marker.exists())
        self.assertFalse((self.scratch / "lane.md").exists())

    def test_a_directory_at_a_result_path_stops_before_the_command(self) -> None:
        (self.scratch / "lane.md").mkdir()
        result = self.lane("--out", "lane.md", "--", "true")
        self.assertEqual(result.returncode, 2)
        self.assertIn("is a directory", result.stderr)

    def test_a_failed_write_leaves_neither_file(self) -> None:
        # The lane replaces the JSON file's directory with a file, so the
        # second write fails after the first was staged.
        source = (
            "import os\n"
            "os.rmdir('j')\n"
            "open('j', 'w').write('x')\n"
        )
        result = self.lane(
            "--cwd",
            str(self.scratch),
            "--out",
            "m/lane.md",
            "--json-out",
            "j/lane.json",
            "--",
            *python_command(source),
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("Traceback", result.stderr)
        self.assertFalse((self.scratch / "m" / "lane.md").exists())
        self.assertEqual(sorted(os.listdir(str(self.scratch / "m"))), [])

    def test_result_files_get_the_usual_permissions(self) -> None:
        self.run_lane(python_command("pass"))
        mask = os.umask(0)
        os.umask(mask)
        for name in ("lane.md", "lane.json"):
            mode = (self.scratch / "out" / name).stat().st_mode & 0o777
            self.assertEqual(mode, 0o666 & ~mask, name)
        self.assertEqual(
            sorted(os.listdir(str(self.scratch / "out"))), ["lane.json", "lane.md"]
        )


class InterruptedWriteTest(RunnerTestCase):
    """What an interruption leaves at each phase of the two renames."""

    def write_with_replace_failing_on(self, call: int) -> tuple:
        spec = importlib.util.spec_from_file_location("run_bounded_review", PROGRAM)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        markdown = self.scratch / "out" / "lane.md"
        record = self.scratch / "out" / "lane.json"
        markdown.parent.mkdir()
        markdown.write_text("earlier markdown")
        record.write_text("earlier json")
        real = os.replace
        calls = []

        def interrupted(source, target):
            calls.append(target)
            if len(calls) == call:
                raise KeyboardInterrupt
            return real(source, target)

        result = {
            "name": "lane",
            "status": "completed",
            "cwd": str(self.scratch),
            "command": ["true"],
            "duration_seconds": 0.0,
            "timeout_seconds": 1.0,
            "idle_seconds": 1.0,
            "return_code": 0,
            "output": "",
        }
        with mock.patch.object(module.os, "replace", interrupted):
            with self.assertRaises(KeyboardInterrupt):
                module.write_result_files(result, markdown, record)
        leftovers = sorted(
            name for name in os.listdir(str(markdown.parent)) if name.endswith(".tmp")
        )
        return markdown.read_text(), record.read_text(), leftovers

    def test_before_the_first_rename_neither_file_changes(self) -> None:
        markdown, record, leftovers = self.write_with_replace_failing_on(1)
        self.assertEqual(markdown, "earlier markdown")
        self.assertEqual(record, "earlier json")
        self.assertEqual(leftovers, [])

    def test_between_the_renames_only_the_markdown_file_is_new(self) -> None:
        markdown, record, leftovers = self.write_with_replace_failing_on(2)
        self.assertTrue(markdown.startswith("# Bounded Code Review Result"))
        self.assertEqual(record, "earlier json")
        self.assertEqual(leftovers, [])


class ProcessGroupTest(RunnerTestCase):
    def test_a_stall_also_stops_a_background_child(self) -> None:
        pid_file = self.scratch / "child.pid"
        source = (
            "import subprocess, sys, time\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            "open(%r, 'w').write(str(child.pid))\n"
            "time.sleep(60)\n" % str(pid_file)
        )
        _, record = self.run_lane(python_command(source), "--idle-seconds", "1")
        self.assertEqual(record["status"], "stalled")
        pid = int(pid_file.read_text())
        deadline = time.monotonic() + 10
        alive = True
        while alive and time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                alive = False
            else:
                time.sleep(0.1)
        self.assertFalse(alive, "the child outlived the stalled lane")

    def load_module(self):
        spec = importlib.util.spec_from_file_location("run_bounded_review", PROGRAM)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_a_group_that_is_gone_ends_the_stop(self) -> None:
        module = self.load_module()
        process = mock.Mock(pid=12345)
        killpg = mock.Mock(side_effect=ProcessLookupError)
        with mock.patch.object(module.os, "killpg", killpg):
            module.terminate_process(process)
        killpg.assert_called_once_with(12345, signal.SIGTERM)
        process.send_signal.assert_not_called()
        process.poll.assert_not_called()

    def test_a_permission_error_from_killpg_signals_the_command_itself(self) -> None:
        # EPERM does not prove the group is gone, so the stop goes on
        # with the command's own process: SIGTERM, then SIGKILL.
        module = self.load_module()
        process = mock.Mock(pid=12345)
        process.poll.return_value = None
        killpg = mock.Mock(side_effect=PermissionError(1, "Operation not permitted"))
        with mock.patch.object(module.os, "killpg", killpg), mock.patch.object(
            module.time, "sleep"
        ), mock.patch.object(module.time, "monotonic", side_effect=[0.0, 0.0, 10.0]):
            module.terminate_process(process)
        self.assertEqual(
            killpg.call_args_list,
            [mock.call(12345, signal.SIGTERM), mock.call(12345, signal.SIGKILL)],
        )
        self.assertEqual(
            process.send_signal.call_args_list,
            [mock.call(signal.SIGTERM), mock.call(signal.SIGKILL)],
        )

    def test_a_command_that_cannot_be_signalled_does_not_hold_the_runner(self) -> None:
        # Neither the group nor the command may be signalled, as for a
        # program another user owns. The runner stops waiting and
        # records no exit code.
        module = self.load_module()
        process = subprocess.Popen(
            ["sleep", "60"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            preexec_fn=os.setsid,
        )
        self.addCleanup(process.stdout.close)
        self.addCleanup(process.wait)
        self.addCleanup(process.kill)
        refused = mock.Mock(side_effect=PermissionError(1, "Operation not permitted"))
        started = time.monotonic()
        with mock.patch.object(module.os, "killpg", refused), mock.patch.object(
            process, "send_signal", refused
        ), mock.patch.object(module, "STOP_WAIT_SECONDS", 0.5):
            status, _output, duration, return_code = module.collect_output(process, 30, 0.5)
        elapsed = time.monotonic() - started
        self.assertEqual(status, "stalled")
        self.assertIsNone(return_code)
        self.assertLess(elapsed, 10)
        self.assertIsNone(process.poll())
        # The idle budget, the wait after SIGTERM and the wait after
        # SIGKILL, each 0.5 seconds here, are all in the duration.
        self.assertGreaterEqual(duration, 1.5)
        self.assertLessEqual(duration, elapsed)


class UsageTest(RunnerTestCase):
    def usage(self, *arguments: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(PROGRAM), *arguments],
            capture_output=True,
            text=True,
            cwd=str(self.scratch),
            timeout=60,
        )

    def test_invalid_arguments_exit_2_and_write_nothing(self) -> None:
        cases = (
            ["--name", "lane", "--out", "lane.md"],
            ["--name", "lane", "--out", "lane.md", "--"],
            ["--name", "lane", "--out", "lane.md", "--timeout-seconds", "0", "--", "true"],
            ["--name", "lane", "--out", "lane.md", "--idle-seconds", "-1", "--", "true"],
            ["--out", "lane.md", "--", "true"],
            ["--name", "lane", "--out", "lane.md", "--timeout-seconds", "inf", "--", "true"],
            ["--name", "lane", "--out", "lane.md", "--timeout-seconds", "nan", "--", "true"],
            ["--name", "lane", "--out", "lane.md", "--idle-seconds", "inf", "--", "true"],
        )
        for arguments in cases:
            with self.subTest(arguments=arguments):
                result = self.usage(*arguments)
                self.assertEqual(result.returncode, 2)
                self.assertIn("usage:", result.stderr)
                self.assertFalse((self.scratch / "lane.md").exists())

    def test_result_names_that_could_be_one_file_are_refused(self) -> None:
        nfc = "\u00e9.md"
        nfd = "e\u0301.md"
        cases = (
            ("lane.md", "lane.md"),
            ("lane.md", "lane.md/x.json"),
            ("d/lane.md", "d"),
            ("Lane.md", "lane.md"),
            ("D/lane.md", "d"),
            (nfc, nfd),
        )
        marker = self.scratch / "ran"
        for out, json_out in cases:
            with self.subTest(out=out, json_out=json_out):
                result = self.usage(
                    "--name", "lane", "--out", out, "--json-out", json_out,
                    "--", *python_command("open(%r, 'w').write('x')" % str(marker)),
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("error: --out and --json-out must be separate files", result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertFalse(marker.exists())
                self.assertEqual(os.listdir(str(self.scratch)), [])

    def test_failures_in_the_result_path_phase_exit_2_without_a_traceback(self) -> None:
        long_name = "a" * 300
        loop = self.scratch / "loop"
        os.symlink("loop", str(loop))
        marker = self.scratch / "ran"
        cases = (
            ["--out", "ok.md", "--json-out", long_name + ".json"],
            ["--out", long_name + ".md", "--json-out", "ok.json"],
            ["--out", long_name + ".md"],
            ["--out", "loop/lane.md"],
            ["--out", "ok.md", "--json-out", "loop/lane.json"],
        )
        for extra in cases:
            with self.subTest(extra=extra):
                result = self.usage(
                    "--name", "lane", *extra,
                    "--", *python_command("open(%r, 'w').write('x')" % str(marker)),
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("error: cannot write", result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertFalse(marker.exists())
                self.assertFalse((self.scratch / "ok.md").exists())
                self.assertFalse((self.scratch / "ok.json").exists())

    def test_two_parent_directories_that_are_one_directory_are_refused(self) -> None:
        spec = importlib.util.spec_from_file_location("run_bounded_review", PROGRAM)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        first = self.scratch / "a"
        second = self.scratch / "b"
        first.mkdir()
        second.mkdir()
        real = os.path.samefile

        def alias(one: str, two: str) -> bool:
            # Stand in for a bind mount: a and b are one directory.
            if {Path(one).name, Path(two).name} == {"a", "b"}:
                return True
            return real(one, two)

        with mock.patch.object(module.os.path, "samefile", alias):
            self.assertTrue(module.same_entry(first / "lane.md", second / "LANE.md"))
            self.assertFalse(module.same_entry(first / "lane.md", second / "lane.json"))

    def test_the_json_file_is_strict_json(self) -> None:
        _, record = self.run_lane(python_command("print('x')"))
        text = (self.scratch / "out" / "lane.json").read_text()

        def refuse(constant: str) -> None:
            raise ValueError(constant)

        json.loads(text, parse_constant=refuse)
        self.assertEqual(record["status"], "completed")

    def test_two_existing_names_for_one_file_are_refused(self) -> None:
        (self.scratch / "first.md").write_text("old")
        os.link(str(self.scratch / "first.md"), str(self.scratch / "second.json"))
        result = self.usage(
            "--name", "lane", "--out", "first.md", "--json-out", "second.json", "--", "true"
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("separate files", result.stderr)
        self.assertEqual((self.scratch / "first.md").read_text(), "old")

    def test_a_result_name_the_file_system_rejects_is_refused(self) -> None:
        marker = self.scratch / "ran"
        result = self.usage(
            "--name",
            "lane",
            "--out",
            "a" * 300 + ".md",
            "--",
            *python_command("open(%r, 'w').write('x')" % str(marker)),
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("cannot write", result.stderr)
        self.assertFalse(marker.exists())

    def test_a_long_result_name_within_the_limit_is_written(self) -> None:
        name = "b" * 250 + ".md"
        result = self.usage("--name", "lane", "--out", name, "--", *python_command("pass"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.scratch / name).is_file())

    def test_a_command_that_cannot_start_exits_1_and_writes_nothing(self) -> None:
        cases = (
            ["--", "multi-persona-code-review-no-such-command"],
            ["--cwd", str(self.scratch / "missing"), "--", "true"],
        )
        for extra in cases:
            with self.subTest(extra=extra):
                result = self.usage("--name", "lane", "--out", "lane.md", *extra)
                self.assertEqual(result.returncode, 1)
                self.assertIn("Traceback", result.stderr)
                self.assertFalse((self.scratch / "lane.md").exists())


if __name__ == "__main__":
    unittest.main()
