---
name: multi-persona-code-review
description: >-
  Run a multi-persona, non-interactive code review of the
  current diff, write the consolidated findings back into the
  source as `TODO(code-review:<id>)` comments, and report a
  severity-ordered (P0–P3) summary. A roster of focused review
  personas (correctness, security, adversarial, reliability,
  performance, architecture, tests, and an l8() tech-lead
  pass, plus opt-in CUJ, plan, and legal/SOC2 compliance
  passes) fans out over one diff scope, then the findings
  merge and land next to the code. Use when the user asks for a
  code review of working-tree changes, a branch diff, or a
  commit.
---

# Multi-Persona Code Review

Canonical, agent-neutral specification for running a
multi-persona, non-interactive code review, writing its
consolidated findings back into the source, and reporting a
summary.

One review fans out a roster of focused personas over a
single diff scope, then merges their findings, writes each
one into the code as a `TODO(code-review:<id>)` comment, and
reports a severity-ordered summary. The codex baseline persona
is Codex CLI's built-in `codex review` subcommand; the other
personas frame that same engine — or a read-only review
subagent — with a specific lens. Write-back is the default; a
caller may request report-only to suppress all writes.

## Invocation

Always invoke the `codex` and `claude-code` CLIs through
`npx`, never a native PATH binary: `npx -y @openai/codex ...`
for Codex and `npx -y @anthropic-ai/claude-code ...` for
Claude Code. This pins every run to the npm package rather
than
whatever binary is on PATH. The command examples below use
this form; keep it for every invocation.

Before the first Claude-engine pass in a session, run a cheap
smoke check from the target repo root:

```sh
npx -y @anthropic-ai/claude-code --version
npx -y @anthropic-ai/claude-code -p "Reply with exactly: ok"
```

These two commands are shown bare and run unbounded. To
bound them, run each through `scripts/run_bounded_review.py`
with the smoke-check budgets under "Default budgets".

If either command fails, do not spend review time on broad
Claude prompts. Reassign those personas to another engine or
mark them `NOT-RUN` with the exact failure. If the smoke
check passes but a repo-reading review prompt produces no
usable output within the run's time budget, interrupt it and
record that persona as timed out rather than implying it
completed.

## Bounded execution contract

Every external agent pass must leave a machine-checkable
result artifact. Do not run open-ended Codex or Claude review
commands directly from a chat session and then summarize from
memory. Use the bundled bounded runner, or an equivalent
wrapper that enforces the same contract:

```sh
python3 <skill-dir>/scripts/run_bounded_review.py \
  --name <persona-name> \
  --cwd /<target-repo> \
  --out <out-dir>/<persona-name>.md \
  --json-out <out-dir>/<persona-name>.json \
  --timeout-seconds 240 \
  --idle-seconds 60 \
  -- <agent command...>
```

`<skill-dir>` is the directory that holds this `SKILL.md`.
`<out-dir>` is an output directory the operator chooses for
the run's artifacts. Keep it outside the reviewed
repository, so the artifacts do not become part of the
working-tree diff under review. The runner creates missing
parent directories of `--out` and `--json-out`.

The wrapper writes a Markdown artifact and, when requested, a
JSON artifact with:

- `name`
- `status`
- `cwd`
- `command`
- `duration_seconds`
- `timeout_seconds`
- `idle_seconds`
- `return_code`
- `output`

Wrapper `status` is one of:

- `completed` - the command exited `0` within both budgets;
- `failed` - the command exited non-zero within both
  budgets;
- `timed_out` - the hard wall-clock budget was exceeded;
- `stalled` - no stdout/stderr arrived within the idle
  budget.

The runner exits `0` when the status is `completed` and `2`
for any other status. It also exits `2`, writing no
artifact and running nothing, when its own arguments are
invalid (including two result paths that could name one
file) or its pre-run check finds a result path it cannot
write. That check is best effort. The lane's stdin is
`/dev/null`, so a CLI that waits for input reads
end-of-file instead. If the command cannot
be started at all (for example the executable or `--cwd`
does not exist), the runner stops with a Python traceback
and writes no artifact; record that lane as `failed` with
the traceback's last line.

Give the shell call that runs the runner its own time limit
above the lane's hard budget plus about ten seconds, or run
it in the background. If the runner itself is interrupted
or killed before it renames the result files into place, it
leaves no new artifact and the lane can keep running;
record that lane as `failed` as well.

The persona's **review status** is derived after reading the
artifact:

- `findings` - complete anchored findings were produced;
- `no_findings` - the persona explicitly found no issues;
- `invalid` - output did not follow the required finding
  format or lacked anchors;
- `timed_out`, `stalled`, or `failed` - copied from the
  wrapper status;
- `not_run` - a prerequisite failed or the persona was
  skipped by scope.

Only `findings` and `no_findings` count as completed review
coverage. `invalid`, `timed_out`, `stalled`, `failed`, and
`not_run` are explicit coverage gaps. A local manual fallback
may be recorded, but it must not overwrite the failed
external lane.

### Default budgets

Use small defaults unless the operator explicitly grants a
larger budget:

- Smoke checks: 60 seconds hard timeout and 30 seconds idle
  timeout.
- Codex baseline: 240 seconds hard timeout and 60 seconds
  idle timeout.
- Persona passes: 180 seconds hard timeout and 60 seconds
  idle timeout.
- Retry after timeout/stall: 120 seconds hard timeout and 45
  seconds idle timeout, with a smaller diff-fed prompt.

If a pass times out or stalls once, retry at most once with a
strictly smaller prompt. If the retry also fails, mark that
persona incomplete and continue. Do not keep extending a
review lane merely because the agent is still consuming
time.

### Required persona output shape

Every persona prompt must ask for this shape and no prose
outside it:

```text
status: findings | no_findings
findings:
- severity: P0|P1|P2|P3
  file: <repo-relative-path>
  line: <line-number-or-anchor>
  issue: <one sentence>
  impact: <why it matters>
  recommendation: <concrete fix direction>
  test: <how to verify>
reviewed_files:
- <repo-relative-path>
commands_run:
- <command-or-none>
confidence: low|medium|high
```

If a persona emits another format, convert only complete,
anchored findings that can be verified. Mark the persona
`invalid` when the output cannot support verification.

### Bounded prompt inputs

Default prompts are diff-bounded. Provide the persona with
the exact review scope, changed-file list, diff stats, and
either:

- a targeted `git diff` for the relevant files; or
- an explicit changed-file list plus short excerpts from the
  required reference documents.

Avoid broad "read the repository" prompts unless the
operator grants time for that lane. Ask for at most three
findings per persona by default; a small anchored set is more
useful than an unfinished broad search.

## Diff scope

Pick one scope; it applies to **every** persona pass. Run
every pass from the target repository root so the diff scope
is correct. The snippets below show only which scope flag
goes with which scope; they are fragments, not commands to
run as-is. The complete bounded commands, with `--yolo`, are
under "Running the passes".

- Working-tree diff (default): staged, unstaged, and
  untracked changes.

  ```text
  npx -y @openai/codex review --uncommitted
  ```

- Branch diff against a base:

  ```text
  npx -y @openai/codex review --base main
  ```

- A single commit:

  ```text
  npx -y @openai/codex review --commit <sha>
  ```

## Persona roster

Each persona reviews the same diff through a fixed lens and
returns findings as: issue, severity (`P0`–`P3`),
`file:line`, impact, and concrete fix direction. The
`Engine` column is the **recommended default**, not a
mandate — the orchestrating agent may reassign a persona's
engine.

| Persona                              | Lens                                                                                                                                                                                | Engine |
| ------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------ |
| Codex baseline                       | General correctness; the unframed `codex review` pass.                                                                                                                              | Codex  |
| Security / pen-tester                | Attacker mindset: injection, authz/authn, secret handling, SSRF, deserialization, supply chain.                                                                                     | Codex  |
| Reliability / failure-modes          | Error handling, retries, idempotency, races, partial failures, observability gaps.                                                                                                  | Codex  |
| Performance / scalability            | Hot paths, N+1 queries, allocations, complexity, blocking I/O, resource limits.                                                                                                     | Codex  |
| Adversarial reviewer                 | Assume the author is wrong; hunt edge cases and hostile inputs that break the change.                                                                                               | Claude |
| API / architecture / maintainability | Interface design, coupling, naming, backward compatibility, long-term maintainability; local reimplementation of utilities an existing library already provides.                     | Claude |
| Test-coverage / data-integrity       | Missing or weak tests, flaky patterns; data, privacy, and migration correctness; PII handling.                                                                                      | Claude |
| l8() tech lead                       | L8 risk-order lens plus the mandatory L9/L10 escalation addendum, from the separate `l8` skill; closes the report. `not_run` without it.                                            | Claude |
| CUJ reviewer (opt-in)                | Alignment with the Critical User Journeys in a supplied CUJ doc; advance/endanger/neutral per journey. Skips without a doc path.                                                    | Claude |
| Plan reviewer (opt-in)               | Alignment with a supplied implementation plan: todos, sequencing, dependencies, completion criteria, introduced blockers. Skips without a doc path.                                 | Claude |
| Legal/SOC2 compliance (opt-in)       | SOC2 controls, regulatory boundary, privacy/PII/data governance, and secrets/disclosure hygiene; grounded in a supplied compliance doc. Skips without a doc path or flag.           | Claude |

### Persona briefs and opt-in inputs

The brief for each persona, the context beyond the diff
hunk that every brief must require, and the rules for the
three opt-in inputs `cuj_doc`, `plan_doc`, and
`compliance_doc` are in
[`references/personas.md`](references/personas.md). Read it
before writing the first persona prompt.

## Running the passes

Invoke every external pass through
`scripts/run_bounded_review.py`. Inside that wrapper,
invoke Codex non-interactively with `--yolo` from the target
repo root, and read findings from the bounded result artifact
and any `-o` file, never the streamed trace alone (plain
`codex review` emits a long exploration log with no parseable
findings block). The key CLI constraint: `codex review` /
`codex exec review` **rejects a custom prompt together with a
scope flag** (`the argument '--uncommitted' cannot be used
with '[PROMPT]'`). So the baseline and the persona passes
take different forms.

- **Codex baseline (no brief):** the scoped review with no
  prompt — codex auto-detects the diff.

  ```sh
  python3 <skill-dir>/scripts/run_bounded_review.py \
    --name codex-baseline \
    --cwd /<target-repo> \
    --out <out-dir>/codex-baseline.bounded.md \
    --json-out <out-dir>/codex-baseline.bounded.json \
    --timeout-seconds 240 \
    --idle-seconds 60 \
    -- npx -y @openai/codex --yolo \
      exec review --uncommitted -o <out-dir>/codex-baseline.md
  ```

  Swap `--uncommitted` for `--base <branch>` or
  `--commit <sha>` as chosen above.

- **Codex-engine persona (with a brief):** drop the scope
  flag and name the scope in the prompt, using plain
  `exec` (which always accepts a prompt):

  ```sh
  python3 <skill-dir>/scripts/run_bounded_review.py \
    --name codex-security \
    --cwd /<target-repo> \
    --out <out-dir>/codex-security.bounded.md \
    --json-out <out-dir>/codex-security.bounded.json \
    --timeout-seconds 180 \
    --idle-seconds 60 \
    -- npx -y @openai/codex --yolo \
    exec -o <out-dir>/codex-security.md \
    "Run \`git diff HEAD\` and inspect untracked files, then
     review ONLY the uncommitted working-tree changes as
     <persona>. <persona brief>. Reply in the required
     persona output shape and nothing else. Review
     only — do not modify any files."
  ```

  Read findings from `<out>.md` (or a Claude subagent's
  structured return), not the streamed trace.

- **Claude-engine persona:** run a read-only review subagent
  or `npx -y @anthropic-ai/claude-code` invocation through
  the bounded runner. The prompt gets the persona brief and
  the same diff scope, with tools limited to reading and
  searching — never editing. Prefer a bounded prompt that
  includes the exact diff or an explicit changed-file list
  plus the few required reference docs. Avoid broad "read the
  repository" prompts unless the operator explicitly grants
  the time budget. Require at most a small number of findings
  per persona so the pass returns review evidence instead of
  spending the whole budget on context gathering.

  ```sh
  python3 <skill-dir>/scripts/run_bounded_review.py \
    --name claude-architecture \
    --cwd /<target-repo> \
    --out <out-dir>/claude-architecture.bounded.md \
    --json-out <out-dir>/claude-architecture.bounded.json \
    --timeout-seconds 180 \
    --idle-seconds 60 \
    -- npx -y @anthropic-ai/claude-code \
      -p "<diff-bounded architecture review prompt>"
  ```

Run passes in parallel where the engine allows. The default
engine split above is guidance; reassign as needed. When an
engine cannot deliver a usable pass, reassign that persona to
the other engine rather than dropping the coverage, and note
any persona that could not run in the report. When a persona
times out, preserve the partial transcript only as diagnostic
evidence; do not treat it as a candidate-finding source
unless it contains complete, anchored findings that pass the
verification stage.

## Consolidation

Merge every persona's findings into one list:

- Deduplicate by `file:line` plus the underlying issue. When
  multiple personas raise the same finding, keep one entry
  and tag it with **all** contributing personas — agreement
  across personas raises confidence.
- Order the merged list by severity from `P0` to `P3`.
- Each entry names its persona tag(s), `file:line`, impact,
  and fix direction.

## Verify before write-back

Persona output is a candidate list, not the final list.
Independent personas reliably produce confident-sounding
false positives, so every finding is verified against the
current source before it is written or reported. Verify each
finding by opening the cited file at the cited anchor and
checking all of:

- **The anchor exists** and matches the described code.
- **The claim holds under the real framework semantics** —
  reproduce the reasoning against how the framework actually
  behaves (auto-waiting assertions, client-side versus
  server-backed state, lazy versus eager validation), not
  against an assumed model.
- **The file is in scope** — tracked and not ignored,
  generated, or a local-only artifact; a finding about an
  ignored or out-of-diff file is dropped.
- **The severity is justified** by concrete impact; downgrade
  speculative findings.

Drop findings that fail verification. Only verified findings
proceed to write-back and the report; mention in the report
how many candidates were dropped as unverified, so the signal
ratio is visible. This stage is mandatory and is the single
biggest lever on review quality.

## Write-back

After consolidation, write each finding back into the
reviewed code as a `TODO(code-review:<id>)` comment, placed
by the hierarchy below. Write-back is the **default**. A
caller may request report-only to suppress all writes and
only print the summary.

The marker `TODO(code-review:<id>)` and the spillover file
name `CODE_REVIEW.gpt.md` keep this skill's original name,
`code-review`, on purpose. The `address-comments` skill,
published separately as `trycopilotai/address-comments`,
looks for exactly these strings; renaming them would stop it
from finding what this skill writes.

The persona passes that produced the findings stay read-only;
write-back is a single post-consolidation step performed by
the orchestrating agent. It inserts comment lines (and, for
spillover, one Markdown queue file) and nothing else — it
never changes code logic, ordering, or any non-comment line.

The marker format, the stable ID, the idempotency rule, and
the placement hierarchy are in
[`references/write-back.md`](references/write-back.md). Read
it before the first write-back.

## Reporting

After write-back, print a summary:

- Lead with counts: inline comments written, file-top
  comments written, `CODE_REVIEW.gpt.md` spillover entries,
  and candidate findings dropped as unverified (so the
  signal ratio is visible).
- List the consolidated findings, ordered `P0` to `P3`, each
  with its marker, persona tag(s), and `path:anchor`. Call
  out `P0` findings prominently; they are the highest signal.
- Note any persona pass that could not run (for example an
  engine that failed to produce a usable result) so the
  coverage gap is explicit.
- For Claude-engine passes, report whether the smoke check
  passed, whether prompts were diff-fed or file-list-bounded,
  and which personas timed out or were reassigned.
- If there are no findings, say so and give a short
  residual-risk note.
- Append the l8() persona's **L9/L10 escalation addendum**
  as the final section of the report (chat only — the
  addendum is not written into code): a standalone `---`
  line, one L9 paragraph on cross-team design quality, risk
  retirement, sequencing, and organizational tradeoffs;
  another standalone `---` line; then one L10 paragraph on
  strategic portfolio impact, long-horizon bets, executive
  risk posture, and business alignment. Keep this addendum
  mandatory even when the review ends with no critical
  findings.
- If the l8() lane is `not_run` because the `l8` skill is not
  installed, the addendum is not produced. Put one line in
  its place that says so, and do not write L9 or L10
  paragraphs without the l8() lane.

## Operational record

The final report keeps every lane's status apart, as
[`references/operational-record.md`](references/operational-record.md)
sets out, and says whether `address-comments` has new
markers to act on. Read it before writing the report.

## Discipline

- Persona passes are read-only: every persona pass, on either
  engine, only produces findings and never edits files.
- Write-back is the only mutation: it inserts
  `TODO(code-review:<id>)` comment lines and may create or
  append `CODE_REVIEW.gpt.md`. It must never change code
  logic, reorder code, or touch any non-comment line. Do not
  stage, commit, or push, and preserve unrelated dirty work
  exactly as found.
- Run every pass from the root of the repository being
  reviewed so the diff scope is correct.

## Validation

- After write-back, run `git diff --check` and confirm every
  added line is a comment line or a `CODE_REVIEW.gpt.md`
  line — there must be no logic diff.
- Scoped-lint the touched files where the repo's linter
  supports it; do not run repo-wide lint unless the user asks
  for broad validation.
