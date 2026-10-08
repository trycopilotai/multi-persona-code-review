#!/usr/bin/env python3
"""Render a raw agent-client log as a readable invocation transcript.

Reads the JSON lines a client wrote while it ran the skill and
prints the prompt, each tool call it recognises (name and
arguments; a Codex item of an unknown type by type name only),
each call's status where the log records one, any text the
agent wrote between calls, and the final message.

Each tool argument and each message the agent wrote between
calls is printed on one line, JSON-escaped, and clipped at
LIMIT characters with a note of how many were cut. The prompt
and the final message are not clipped or escaped; each is
reproduced with its trailing newlines trimmed and nothing else
changed apart from the transforms below. The final message is
the `result` text of a Claude Code log and the last
`agent_message` of a Codex log.

Supported logs:

    claude-code   `claude --print --verbose --output-format stream-json`
    codex         `codex exec --json`

Usage:

    python3 scripts/render_invocation.py --client claude-code \\
        --prompt prompt.txt --capture-root /abs/fixture \\
        --plugin-root /abs/plugin --home /abs/home \\
        --hostname name raw.jsonl > transcript.txt

A run made in an isolated temporary directory also passes
`--isolation-root` once for each spelling of that directory, and
names its plugin and capture roots under `/iso`.

Every value the transforms use is passed on the command line, so
the same raw log and the same arguments always give the same
bytes. The transforms are applied, in this order, to the prompt
and to every string value in each decoded log event (including
strings nested in lists and objects) before anything is clipped.
They are not applied to dictionary keys, so a path or host name
that appears only as a key in the log is printed unchanged.
Each replaces a whole path prefix (or a whole host name), never
part of a longer name:

    replace-isolation-root each --isolation-root             -> /iso
    replace-scratch-root   /private/tmp/claude-<uid>/<slug>  -> /scratch
                           (and the same under /tmp/)
    replace-plugin-root    each --plugin-root                -> /plugin
    replace-capture-root   --capture-root                    -> /work
    replace-home           --home                            -> ~
    replace-hostname       --hostname, and its first label   -> host

Apart from the transforms, the clipping and escaping above and
the trimmed trailing newlines, nothing is changed. What the
renderer leaves out: tool results (only their status is
printed); in a Claude Code log, thinking blocks, empty text
blocks, user content other than tool results, and every event
other than `system` `init`, `assistant`, `user` and `result`;
in a Codex log, every event other than `thread.started`,
`item.completed` and `turn.completed`. A completed Codex item
of an unknown type is printed by type name only.

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

LIMIT = 400  # characters kept of each rendered argument value

# A path prefix ends where a path-name character would continue it.
_END = r"(?![A-Za-z0-9_.\-])"
_SCRATCH = re.compile(r"(?:/private)?/tmp/claude-[0-9]+/[^/\s\"'`]+")


class Transforms:
    def __init__(self, capture_root, plugin_roots, home, hostname, isolation_roots=()):
        self.steps = []
        for root in sorted(isolation_roots, key=len, reverse=True):
            self.steps.append((self._prefix(root), "/iso"))
        self.steps.append((_SCRATCH, "/scratch"))
        for root in sorted(plugin_roots, key=len, reverse=True):
            self.steps.append((self._prefix(root), "/plugin"))
        self.steps.append((self._prefix(capture_root), "/work"))
        self.steps.append((self._prefix(home), "~"))
        names = [hostname]
        short = hostname.split(".")[0]
        if short != hostname:
            names.append(short)
        for name in names:
            self.steps.append(
                (re.compile(r"(?<![A-Za-z0-9_.\-])" + re.escape(name) + _END), "host")
            )

    @staticmethod
    def _prefix(path):
        return re.compile(re.escape(path.rstrip("/")) + _END)

    def __call__(self, text):
        for pattern, replacement in self.steps:
            text = pattern.sub(replacement, text)
        return text


def clip(value):
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
    text = json.dumps(text, ensure_ascii=False)[1:-1]
    if len(text) > LIMIT:
        text = text[:LIMIT] + " ...[%d more characters]" % (len(text) - LIMIT)
    return text


def call_lines(name, arguments, depth):
    pad = "  " * depth
    lines = [pad + "> " + name]
    for key in sorted(arguments):
        lines.append(pad + "    " + key + ": " + clip(arguments[key]))
    return lines


def render_claude_code(events):
    header, body, final = [], [], None
    depth_of = {}
    for event in events:
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            header.append("client version: " + str(event.get("claude_code_version")))
            header.append("model: " + str(event.get("model")))
        elif kind == "assistant":
            parent = event.get("parent_tool_use_id")
            depth = depth_of.get(parent, 0) + (1 if parent else 0)
            for block in event["message"].get("content", []):
                if block.get("type") == "tool_use":
                    depth_of[block["id"]] = depth
                    tag = "[subagent] " if parent else ""
                    body.extend(call_lines(tag + block["name"], block.get("input", {}), depth))
                elif block.get("type") == "text" and block.get("text", "").strip():
                    pad = "  " * depth
                    tag = "[subagent] " if parent else ""
                    body.append(pad + tag + "agent: " + clip(block["text"]))
        elif kind == "user":
            content = event.get("message", {}).get("content", [])
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                pad = "  " * depth_of.get(block.get("tool_use_id"), 0)
                if block.get("is_error"):
                    text = block.get("content")
                    text = text if isinstance(text, str) else json.dumps(text)
                    match = re.match(r"Exit code (\d+)", text)
                    status = "error (exit %s)" % match.group(1) if match else "error"
                else:
                    status = "ok"
                body.append(pad + "  < " + status)
        elif kind == "result":
            final = event.get("result", "")
            for key in ("subtype", "num_turns", "duration_ms", "total_cost_usd"):
                header.append("result %s: %s" % (key, event.get(key)))
    return header, body, final


def render_codex(events):
    header, body, final = [], [], None
    for event in events:
        kind = event.get("type")
        if kind == "thread.started":
            header.append("log format: codex exec --json")
        elif kind == "turn.completed":
            usage = event.get("usage", {})
            for key in sorted(usage):
                header.append("usage %s: %s" % (key, usage[key]))
        elif kind == "item.completed":
            item = event["item"]
            itype = item.get("type")
            if itype == "command_execution":
                body.extend(call_lines("command_execution", {"command": item["command"]}, 0))
                body.append("  < exit %s" % item.get("exit_code"))
            elif itype == "file_change":
                changes = [c.get("kind", "") + " " + c.get("path", "") for c in item.get("changes", [])]
                body.extend(call_lines("file_change", {"changes": "; ".join(changes)}, 0))
                body.append("  < " + str(item.get("status")))
            elif itype == "agent_message":
                final = item.get("text", "")
                body.append("agent: " + clip(final))
            elif itype == "todo_list":
                done = sum(1 for entry in item.get("items", []) if entry.get("completed"))
                body.append("todo_list: %d of %d done" % (done, len(item.get("items", []))))
            else:
                body.extend(call_lines(str(itype), {}, 0))
    return header, body, final


RENDERERS = {"claude-code": render_claude_code, "codex": render_codex}


def deep(value, transforms):
    """Apply the transforms to every string value in a decoded event.

    Dictionary keys are left as they are.
    """
    if isinstance(value, str):
        return transforms(value)
    if isinstance(value, list):
        return [deep(item, transforms) for item in value]
    if isinstance(value, dict):
        return {key: deep(item, transforms) for key, item in value.items()}
    return value


def render(client, prompt, raw, transforms):
    events = [deep(json.loads(line), transforms) for line in raw.splitlines() if line.strip()]
    header, body, final = RENDERERS[client](events)
    prompt = transforms(prompt)
    out = ["client: " + client, *header, "", "## prompt", "", prompt.rstrip("\n"), ""]
    out += [
        "## tool calls and agent text (each argument and message clipped at %d characters)"
        % LIMIT,
        "",
        *body,
        "",
    ]
    out += ["## final message", "", (final or "").rstrip("\n"), ""]
    return "\n".join(out)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--client", choices=sorted(RENDERERS), required=True)
    parser.add_argument("--prompt", required=True, type=Path)
    parser.add_argument("--capture-root", required=True)
    parser.add_argument("--plugin-root", action="append", default=[])
    parser.add_argument("--isolation-root", action="append", default=[])
    parser.add_argument("--home", required=True)
    parser.add_argument("--hostname", required=True)
    parser.add_argument("raw", type=Path)
    args = parser.parse_args(argv)
    transforms = Transforms(
        args.capture_root, args.plugin_root, args.home, args.hostname, args.isolation_root
    )
    text = render(
        args.client,
        args.prompt.read_text(encoding="utf-8"),
        args.raw.read_text(encoding="utf-8"),
        transforms,
    )
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
