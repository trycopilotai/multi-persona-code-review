# Contributing

This repository is one skill, one program with its tests,
and the scripts that build and check the demo images.

## Run the checks first

```sh
make check
```

That runs `tests/test_run_bounded_review.py` and
`tests/test_integrations.py`. With `make`, they need
`python3` and `git` and nothing else, on a POSIX system.
The first runs the
program on short Python commands in temporary directories
and takes a few seconds, because some lanes wait out their
budgets. The second needs a real clone with its history,
because it reads `git log`; when `HEAD` carries a release
tag, it also checks that tag against the manifests.

**The packaging contract asserts on the README.** These will
fail on an innocent-looking prose edit:

- the claim line at the top of the README must appear
  verbatim, and the statuses it describes must be in the
  recorded transcript;
- each install block must carry its own `release=` pin at
  the version both plugin manifests ship;
- the three opt-in input names must appear in the README,
  `SKILL.md` and `references/personas.md`;
- the README and `SECURITY.md` must name every file under
  `references/`;
- `SKILL.md` must stay under 500 lines;
- `evidence/demo-manifest.json` records the SHA-256 of
  `SKILL.md`, of the program and of the transcript, so any
  edit to one of those three files, prose included, fails
  until the manifest is refreshed as described next.

If you change one of those, change the thing it describes
too.

## Changing the skill, the program or the transcript

After any edit to `SKILL.md` or to the program, run:

```sh
make record
make demo
```

`make record` runs `scripts/record_session.py`. It replays
the commands listed in the manifest in a throwaway
directory, writes the transcript with that directory's path
replaced by `/work`, and rewrites the manifest's hashes,
date and interpreter. It needs `bash`, `sh`, `cat`,
`git`, `grep`, `sed`, `sleep` and `python3`, and takes a few
seconds. `make demo` rebuilds the
two images from the transcript. `make assets` rebuilds the
social preview and needs Chrome or Chromium;
`make asset-check` does not.

## What is most useful

Open an issue for any of these. The labels
`good first issue` and `help wanted` mark the ones that are
ready to pick up.

- **A lane the runner records with the wrong status.** Give
  the command, the two budgets, and the status you expected.
- **A persona brief that produces mostly false positives.**
  Attach the diff, with anything private removed, the
  persona, and the findings it returned.
- **A run where an agent changed more than comment lines.**
  Say which host ran it and what it changed.

## Pull requests

Prose changes to `SKILL.md` and the files under
`references/` are welcome. Say what an agent did before the
change and what it does after, on the same diff.

Keep `SKILL.md` under 500 lines; the suite enforces it.
Frontmatter carries `name` and `description` and nothing
else.

Do not rename the marker `TODO(code-review:<id>)` or the
file `CODE_REVIEW.gpt.md`; the `address-comments` skill
looks for both.

The top-level `skill` is a symlink to
`skills/multi-persona-code-review/`. Do not reverse that
orientation.

Commit with your own identity and no `Co-authored-by`
trailer of any kind. The suite fails on one anywhere in
history, so do not apply review suggestions through the
GitHub UI.
