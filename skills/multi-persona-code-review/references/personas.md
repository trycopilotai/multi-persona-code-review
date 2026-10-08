# Persona briefs

Use these as the review prompt (Codex) or subagent brief
(Claude). Each brief is read-only and asks only for
findings.

Every brief must also require context beyond the diff hunk,
because reviewing a hunk in isolation is the main source of
false positives. Each persona must: read the surrounding and
whole changed file (not just the `+`/`-` lines); confirm the
runtime and framework model before flagging (for example a
test runner that auto-waits on assertions, or a client-side
mock versus a server-backed store — claims of races or
collisions depend on which it is); and confirm a flagged file
is actually in scope (tracked and not ignored) rather than a
local or generated artifact. State each finding with the
exact `file:line` anchor and the evidence that supports it so
it can be verified.

- **Codex baseline** — no brief; the plain `codex review`
  pass.
- **Security / pen-tester** — "Review as a penetration
  tester. Find injection, authentication and authorization
  flaws, secret or credential mishandling, SSRF, unsafe
  deserialization, and supply-chain risk. Assume hostile
  input."
- **Reliability / failure-modes** — "Review failure modes:
  error handling, retries and idempotency, race conditions,
  partial-failure recovery, and observability gaps."
- **Performance / scalability** — "Review for performance
  and scalability: hot paths, N+1 queries, excess
  allocations, algorithmic complexity, blocking I/O, and
  resource limits."
- **Adversarial reviewer** — "Review adversarially. Assume
  the author is wrong. Hunt the edge cases and hostile
  inputs that break this change, and name the exact input or
  sequence that triggers each failure."
- **API / architecture / maintainability** — "Review
  interface design, coupling, naming, backward
  compatibility, and long-term maintainability. Flag every
  locally defined helper that reimplements an operation the
  language, its standard library, or an established library
  in this repository already provides. String utilities are
  the most common case: equality, contains or list
  membership, normalization, case folding, splitting,
  joining, and trimming. Before accepting any local helper,
  search the repository's shared utility libraries (for
  example a common string-utilities header, in repos that
  have one) and the standard library for an
  existing equivalent. Do not accept a local
  reimplementation because it is short, private, or correct;
  it is still duplicated surface that drifts. Name the exact
  existing replacement and the dependency the change must
  add, or state explicitly that no equivalent exists."
- **Test-coverage / data-integrity** — "Review test coverage
  and data integrity: missing or weak tests, flaky patterns,
  and data, privacy, and migration correctness including PII
  handling."
- **l8() tech lead** — apply the L8 risk-order lens
  (correctness, then security and data handling, then
  concurrency and reliability, then performance, then API
  and architecture, then testability and observability) and
  append the mandatory L9/L10 escalation addendum described
  under "Reporting." The lens and addendum come from the
  `l8` skill, which is separate and published as
  `trycopilotai/l8`; reuse it as the source rather than
  copying its text. If `l8` is not installed, do not
  improvise the pass: record the l8() lane as `not_run`
  with the reason `l8 skill not installed`.
- **CUJ reviewer (opt-in)** — "If a CUJ document path is
  supplied, read it and review whether this change advances,
  endangers, or is neutral to each listed Critical User
  Journey. Cite the specific journey for every finding. If no
  CUJ doc path is supplied, report 'CUJ doc not in scope' and
  skip." Runs only when the orchestrator passes a CUJ doc
  path (see "Optional doc-path inputs").
- **Plan reviewer (opt-in)** — "If an implementation plan
  path is supplied, read it and review the change against the
  plan's todos, sequencing, dependencies, and completion
  criteria. Flag drift from the plan, skipped steps, and
  blockers the change introduces. If no plan path is
  supplied, report 'Plan doc not in scope' and skip." Runs
  only when the orchestrator passes a plan doc path.
- **Legal/SOC2 compliance (opt-in)** — "Review across four
  lenses: SOC2 controls (audit-trail and log integrity,
  access control and least-privilege, encryption in transit
  and at rest, data retention, change management); regulatory
  boundary (flag automated scoring, approval or pass labels,
  eligibility decisions, recommendations, or any output
  implying a legal or regulatory judgment that software must
  not make); privacy, PII, and data governance (PII
  exposure, data minimization, consent and retention,
  cross-tenant leakage, sensitive-field logging); and secrets
  and disclosure hygiene (hardcoded secrets or tokens,
  sensitive data in logs or errors, private-versus-shared
  boundary leaks). If a compliance reference doc path is
  supplied, ground and cite findings in it. If neither a
  compliance doc path nor an explicit compliance flag is
  supplied, report 'Compliance review not in scope' and
  skip." Runs only when the orchestrator passes a compliance
  doc path or sets the compliance flag.

## Optional doc-path inputs

The CUJ, plan, and legal/SOC2 personas are opt-in: they run
only when the orchestrating agent supplies their input. This
keeps a bare review in any repo quiet — none of the three
fire by default. The orchestrator may pass:

- `cuj_doc`: a CUJ document path (activates the CUJ
  reviewer);
- `plan_doc`: an implementation plan path (activates the
  plan reviewer);
- `compliance_doc`: a compliance reference doc path, or an
  explicit compliance flag in its place (activates the
  legal/SOC2 reviewer).

Those three names are how a caller names the inputs, for
example `plan_doc=/abs/path/plan.md` in the request. No
bundled program reads them; the orchestrating agent passes
each path into its persona's prompt.

These inputs may point at docs outside the reviewed repo (for
example in a separate private working repo), so the
orchestrator passes absolute paths rather than relying on a
persona to discover them in the diff tree. A persona whose
trigger is absent self-skips, and that skip is noted in the
report exactly like a persona pass that could not run.
