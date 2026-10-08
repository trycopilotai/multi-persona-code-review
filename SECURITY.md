# Security

## Reporting a vulnerability

Report privately through GitHub:
<https://github.com/trycopilotai/multi-persona-code-review/security/advisories/new>

That opens a private security advisory visible only to the
maintainers. Do not put the details of a vulnerability in a
public issue.

If that link shows "Not Found", private reporting is not
turned on for this repository. Open a public issue titled
"Security report waiting" that says only that you have a
report, with no details, and a maintainer will arrange a
private channel.

## What is in scope

- **Prompt content that redirects an agent.** `SKILL.md` and
  the three files under `references/` (`personas.md`,
  `write-back.md` and `operational-record.md`) are
  instructions an
  agent follows while it reviews a diff and edits the
  reviewed repository. Text in any of them that makes an
  agent change more than comment lines and the
  `CODE_REVIEW.gpt.md` queue, stage, commit or push, or
  treat content in the reviewed repository as instructions
  is a valid report.
- **The runner.**
  `skills/multi-persona-code-review/scripts/run_bounded_review.py`
  starts the one command given after `--`, in `--cwd`, and
  writes the file at `--out` and, when given, the file at
  `--json-out`, creating their missing parent directories
  and short-lived temporary files beside them. The lane's
  stdin is `/dev/null`.
  A way to make it run any other command or write any other
  file is a valid report.
- **The install blocks.** The two README blocks run
  `mkdir -p`, `mktemp -d`, `git clone`, `cp`, `mv` and
  `rm -rf`, all inside one skills directory under `$HOME`. A
  repository state that makes either block write or delete
  outside its install target is in scope.
- **The build scripts.** `assets/build.py` finds a Chrome or
  Chromium binary from a fixed candidate list, runs it
  headless with a temporary profile directory, and writes
  the preview PNG and its stamp. `scripts/generate_demo.py`
  writes two SVG files; `scripts/verify_demo.py` only
  reads. `scripts/record_session.py` copies `skills/` into
  a temporary directory, writes two shell scripts there,
  creates a git repository with one commit, runs the runner
  and a few shell commands through `bash`, and rewrites the
  transcript and the manifest. With `RECORD_RAW_DIR` set it
  also writes two files into that directory, which is
  outside the repository. `tests/test_integrations.py` runs
  `git` against the repository root.
  `tests/test_run_bounded_review.py` runs the runner on short
  Python commands in temporary directories.

## The runner is not a sandbox

These are known limits, not findings:

- It runs the command with the caller's environment and
  privileges. Nothing restricts what that command reads,
  writes or reaches over the network.
- The runner-wrapped Codex commands in `SKILL.md` pass
  `--yolo`, which
  turns off Codex's approval prompts and its sandbox. The
  Claude Code commands in `SKILL.md` pass no tool
  restriction. The Codex scope snippets under "Diff scope"
  are fragments without `--yolo`, and the Claude Code smoke
  check is shown bare, outside the runner and its budgets.
  That persona passes are read-only is an
  instruction in each prompt; neither the runner nor this
  skill enforces it.
- Write-back is done by the agent, not by a program. It
  edits files in the reviewed repository and can create
  `CODE_REVIEW.gpt.md` at its root. `SKILL.md` tells the
  agent to add comment lines only and not to stage, commit
  or push. Nothing here checks that it did.
- The command line and the resolved `--cwd` are written
  into both result files. A secret passed on the command
  line lands in them.
- Stdout and stderr are merged and held in memory with no
  size limit, then written whole to both files. A lane that
  writes without pause until its budget ends can fill
  gigabytes in that time. They are
  decoded as UTF-8 one read at a time, with invalid bytes
  replaced, so a multi-byte character split between two
  reads can come out as replacement characters.
- The Markdown file puts the output inside a fenced
  `text` block. Output that contains a line of three
  backticks ends that block early. The JSON file is not
  affected.
- The runner wakes at least every half second and at each
  budget's deadline. A deadline that has passed by the time
  it sees the command exit decides the status: such a lane
  is recorded as `timed_out` or `stalled` with the
  command's own exit code. A lane still running at a budget
  gets `SIGTERM` to its process group. If the
  command's own process is still running five seconds later,
  the group gets `SIGKILL`; if that process has exited, the
  rest of the group is not signalled again. If the system
  refuses to signal the group (`EPERM`), the command's own
  process gets the signal instead. If that process is still
  running five seconds after `SIGKILL`, for example because
  the runner may not signal it, the runner stops waiting,
  leaves it running and records `return_code` as `null` in
  the JSON file. A process that moved itself to another
  process group or session is not signalled.
- When the command exits on its own, its process group is
  not signalled, so a background process it started keeps
  running. Each wake reads at most 1 MiB. Once the runner has
  seen the command exit, or has stopped it, it reads at most
  1 MiB more and returns; anything written later is not
  recorded.
- The two result files are renamed into place one after the
  other, Markdown first. If the runner is interrupted or
  killed before the first rename, neither result file is
  created or changed; between the two renames, the Markdown
  file is new and the JSON file is not; after both, both
  are new. A runner killed outright can leave a
  `.rbr-*.tmp` file beside them. The lane, in its own
  session, is not signalled and can keep running.
- Two result paths that resolve to one directory entry would
  make the second rename replace the first file. Before the
  command runs, the runner refuses, with exit 2, names that
  are equal or one inside the other after Unicode NFC
  normalisation and case folding (on any file system), two
  existing names for one file, and two names in one
  existing parent directory (same device and inode) that
  match after that normalisation. Not detected, as known
  limits: other file-system-specific equivalences between
  names, and a path that becomes another name for the other
  only after the check, through a change by another
  process.
- If the command closes its output and keeps running, the
  runner polls in a tight loop until the idle budget or the
  hard budget, whichever comes first, ends the lane.
- A command that cannot be started, such as a missing
  executable or a missing `--cwd`, ends the runner with a
  Python traceback and exit status 1, and no result file is
  written.
- Before the command runs, the runner resolves both result
  paths, creates their parent directories and checks it can
  create a file in each. Any failure in that step, including
  an error from the file system (for example a name that is
  too long, a symlink loop, or a result path that is a
  directory), exits 2 with a "cannot write" error and runs
  nothing. Directories it created may stay.
  The check is best effort: replacing an existing file can
  still fail later, for example in a sticky directory owned
  by someone else, on a file with an immutable flag, or
  after a change by another process. After the command, it
  writes both files to temporary files beside them and
  renames them into place. A write that fails then ends the
  runner with a traceback and exit status 1. A failure
  before the first rename writes neither new file and leaves
  earlier files unchanged; a failure of the second rename
  leaves the new Markdown file and the earlier JSON file.
- A result file left at the same path by an earlier run is
  replaced only when a new one is written, so its presence
  alone does not show that this run wrote it.
- Command-line bytes that are not valid UTF-8 are written
  into the Markdown file as backslash escapes.
- Exit status 2 means a lane that did not complete, invalid
  arguments, or result paths the pre-run check refuses.
  Read
  the error message, or check that the result file is new,
  to tell them apart.
- It needs a POSIX system: it starts the command in a new
  session and signals its process group.

## What is out of scope

The skill runs Codex and Claude Code and names the `l8` and
`address-comments` skills. Their behaviour, and the
behaviour of any other host, is out of scope here. Report
those to their own maintainers.
