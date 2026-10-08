# Operational record

The final report must distinguish every lane:

- `completed: findings`
- `completed: no_findings`
- `invalid`
- `timed_out`
- `stalled`
- `failed`
- `not_run`

Do not collapse these into "review ran." A timed-out external
review plus a manual local fallback is reported as:

```text
External review: timed_out
Manual fallback: completed
```

When the host repo has a private review log or handoff
protocol, append the same lane table there with the bounded
artifact paths and the accepted/deferred findings. If the
host repo has no such protocol, include the table in the
chat report.

If write-back creates actionable `TODO(code-review:<id>)`
comments, the next `address-comments` pass should consume or
record them according to the host repo's workflow. If
write-back is suppressed or no verified findings exist, state
that `address-comments` has no new review comments to act on.
