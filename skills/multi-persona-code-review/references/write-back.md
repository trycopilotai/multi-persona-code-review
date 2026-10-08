# Write-back marker and placement

## Marker and stable ID

Each finding gets one deterministic 12-character ID built
from stable fields — review scope, source-origin path (the
generator, schema, or input when different from the target),
repo-relative target path, a stable semantic anchor (symbol,
schema field, or reviewed hunk identity), and a stable issue
key describing the bug class. Do not derive the ID from
severity, title, or wording, which are mutable.

```sh
printf '%s' "$scope|$origin_path|$target_path|$anchor|$issue_key" \
  | git hash-object --stdin | cut -c1-12
```

Write the finding with a comment leader valid for the target
file's language, wrapping across comment lines when long:

```text
<leader> TODO(code-review:<id>) [P#] <finding>. Impact: <why>. Fix: <direction>. Personas: <tags>.
```

`P#` is `P0`–`P3`. Common leaders: `#` (Python, shell, YAML,
Ruby), `//` (TS, JS, Go, Rust, C, C++, Java), `--` (SQL,
Lua), `<!-- -->` (HTML, Markdown, XML), `/* */` (CSS). When
the target language has no safe comment syntax (for example
JSON), route the finding to spillover.

**Idempotency:** before writing a marker, search the worktree
and `CODE_REVIEW.gpt.md` for that exact
`TODO(code-review:<id>)`. If it already exists, update that
one entry only when the finding adds clearer impact, fix, or
evidence; never duplicate. Do not remove existing markers
unless the user asks for review-state cleanup.

## Placement hierarchy

1. **Line anchor.** Insert the comment on the line(s)
   immediately above the smallest safe origin — the affected
   line, declaration, block, test case, or schema field. This
   is the default for findings tied to specific code.
2. **File top.** When the finding pertains to a file but not
   one line, or the precise line anchor no longer matches,
   insert the comment at the top of that file, below any
   shebang, license header, or package/module declaration,
   using the file's comment syntax.
3. **Spillover `CODE_REVIEW.gpt.md`.** For repo-wide or
   cross-file findings, or a target file that is binary,
   generated, or comment-unsafe, append to a checklist headed
   `## Review Queue` in a `CODE_REVIEW.gpt.md` file at the
   reviewed repo root, ordered `P0`→`P3` then by path and
   idempotent by marker:

   ```text
   - [ ] TODO(code-review:<id>) [P#] <path-or-(repo-wide)>:<anchor> - <finding>. Impact: <why>. Fix: <direction>. Evidence: <context>. Personas: <tags>.
   ```

   When first created, the file is a `# Code Review` title
   followed by `## Review Queue`. The `.gpt` infix marks it
   AI-generated.
