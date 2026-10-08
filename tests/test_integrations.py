#!/usr/bin/env python3
"""The packaging contract.

Facts this repository states in more than one place are
pinned here where a script can compare them: the name and
version, the claim and the transcript behind it, the demo
images, the install blocks, and the evidence hashes.

Runs offline with the standard library and `git`:

    python3 tests/test_integrations.py
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import struct
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "multi-persona-code-review"
PACKAGE = ROOT / "skills" / NAME
PROGRAM = PACKAGE / "scripts" / "run_bounded_review.py"
SKILL = PACKAGE / "SKILL.md"
README = ROOT / "README.md"
TRANSCRIPT = ROOT / "evidence" / "transcripts" / "bounded-session.txt"
MANIFEST = ROOT / "evidence" / "demo-manifest.json"
RECORDER = ROOT / "scripts" / "record_session.py"
RENDERER = ROOT / "scripts" / "render_invocation.py"
INVOCATION_TRANSFORMS = [
    "replace-scratch-root",
    "replace-plugin-root",
    "replace-capture-root",
    "replace-home",
    "replace-hostname",
]
CLAIM = "Stalled and timed-out lanes still write a status file."
STATUS_LINES = [
    'out/endless.json:  "status": "timed_out",',
    'out/failing.json:  "status": "failed",',
    'out/finding.json:  "status": "completed",',
    'out/silent.json:  "status": "stalled",',
]
REFERENCES = (
    "references/operational-record.md",
    "references/personas.md",
    "references/write-back.md",
)
OPT_IN_INPUTS = ("cuj_doc", "plan_doc", "compliance_doc")
MARKER = "TODO(code-review:<id>)"
REPOSITORY = "https://github.com/trycopilotai/" + NAME


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def manifest(product: str) -> dict:
    return json.loads(read(ROOT / product / "plugin.json"))


def frontmatter(text: str) -> dict:
    """The `key: value` pairs between the two `---` lines."""
    lines = text.splitlines()
    if lines[0] != "---":
        raise AssertionError("SKILL.md does not open with frontmatter")
    end = lines.index("---", 1)
    fields: dict = {}
    key = None
    for line in lines[1:end]:
        match = re.match(r"^([a-z_-]+):\s*(.*)$", line)
        if match:
            key = match.group(1)
            fields[key] = match.group(2).strip()
            continue
        if key is None or not line.startswith(" "):
            raise AssertionError("unexpected frontmatter line: " + line)
        fields[key] = (fields[key] + " " + line.strip()).strip()
    for name, value in fields.items():
        if value.startswith(">-"):
            fields[name] = value[2:].strip()
    return fields


def interface_yaml(text: str) -> dict:
    """The quoted scalars under `interface:` in agents/openai.yaml."""
    lines = text.splitlines()
    if lines[0] != "interface:":
        raise AssertionError("openai.yaml does not start with interface:")
    fields: dict = {}
    key = None
    for line in lines[1:]:
        match = re.match(r"^  ([a-z_]+):\s*(.*)$", line)
        if match:
            key = match.group(1)
            fields[key] = match.group(2).strip()
            continue
        fields[key] = (fields[key] + " " + line.strip()).strip()
    for name, value in fields.items():
        if not (value.startswith('"') and value.endswith('"')):
            raise AssertionError(name + " is not a double-quoted scalar")
        fields[name] = value[1:-1]
    return fields


def install_blocks() -> list:
    return re.findall(r"```sh\nset -eu\n(.*?)```", read(README), flags=re.S)


class LayoutTest(unittest.TestCase):
    def test_skill_is_a_symlink_into_the_canonical_package(self) -> None:
        link = ROOT / "skill"
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(str(link)), "skills/" + NAME)
        self.assertFalse(PACKAGE.is_symlink())

    def test_readme_and_security_name_every_reference_file(self) -> None:
        readme = read(README)
        security = read(ROOT / "SECURITY.md")
        for relative in REFERENCES:
            self.assertIn(relative, readme)
            self.assertIn(relative.split("/")[-1], security)
        on_disk = sorted(
            "references/" + p.name for p in (PACKAGE / "references").iterdir()
        )
        self.assertEqual(on_disk, sorted(REFERENCES))

    def test_package_holds_what_the_readme_says_it_installs(self) -> None:
        for relative in (
            "SKILL.md",
            "agents/openai.yaml",
            "scripts/run_bounded_review.py",
        ) + REFERENCES:
            self.assertTrue((PACKAGE / relative).is_file(), relative)

    def test_history_has_no_co_author_trailer(self) -> None:
        messages = git("log", "--all", "--format=%B")
        self.assertNotIn("co-authored-by", messages.lower())


class SkillTest(unittest.TestCase):
    def test_frontmatter_is_name_and_description_only(self) -> None:
        fields = frontmatter(read(SKILL))
        self.assertEqual(sorted(fields), ["description", "name"])
        self.assertEqual(fields["name"], NAME)
        self.assertRegex(NAME, r"^[a-z0-9]+(-[a-z0-9]+)*$")
        self.assertLessEqual(len(NAME), 64)
        self.assertTrue(fields["description"])
        self.assertLessEqual(len(fields["description"]), 1024)

    def test_skill_stays_under_five_hundred_lines(self) -> None:
        self.assertLess(len(read(SKILL).splitlines()), 500)

    def test_examples_write_to_an_operator_chosen_directory(self) -> None:
        text = read(SKILL)
        self.assertNotIn("/tmp/", text)
        self.assertIn("--out <out-dir>/", text)
        self.assertIn("python3 <skill-dir>/scripts/run_bounded_review.py", text)

    def test_files_the_skill_points_at_exist(self) -> None:
        text = read(SKILL)
        for relative in ("scripts/run_bounded_review.py",) + REFERENCES:
            self.assertIn(relative, text)
            self.assertTrue((PACKAGE / relative).is_file(), relative)


class ManifestTest(unittest.TestCase):
    def test_both_manifests_agree(self) -> None:
        claude = manifest(".claude-plugin")
        codex = manifest(".codex-plugin")
        for field in (
            "name",
            "version",
            "description",
            "license",
            "homepage",
            "repository",
            "skills",
        ):
            self.assertEqual(claude[field], codex[field], field)
        self.assertEqual(claude["name"], NAME)
        self.assertEqual(claude["skills"], "./skills/")
        self.assertEqual(claude["repository"], REPOSITORY)
        self.assertEqual(claude["license"], "MIT")
        self.assertRegex(claude["version"], r"^\d+\.\d+\.\d+$")

    def test_a_release_tag_on_head_is_the_manifest_version(self) -> None:
        tags = git("tag", "--points-at", "HEAD").split()
        releases = [tag for tag in tags if tag.startswith("v")]
        if not releases:
            self.skipTest("HEAD carries no release tag")
        self.assertEqual(releases, ["v" + manifest(".claude-plugin")["version"]])

    def test_codex_interface_matches_the_agent_file(self) -> None:
        interface = manifest(".codex-plugin")["interface"]
        for field in (
            "displayName",
            "shortDescription",
            "longDescription",
            "developerName",
            "category",
            "websiteURL",
        ):
            self.assertTrue(interface.get(field), field)
        self.assertLessEqual(len(interface["shortDescription"]), 30)
        prompts = interface["defaultPrompt"]
        self.assertEqual(len(prompts), 1)
        self.assertIn("$" + NAME, prompts[0])
        agent = interface_yaml(read(PACKAGE / "agents" / "openai.yaml"))
        self.assertEqual(agent["default_prompt"], prompts[0])
        self.assertEqual(agent["display_name"], interface["displayName"])
        self.assertEqual(agent["short_description"], interface["shortDescription"])


class ReadmeTest(unittest.TestCase):
    def test_claim_is_on_its_own_line(self) -> None:
        self.assertIn(CLAIM, read(README).splitlines())

    def test_transcript_shows_a_status_file_for_the_stalled_and_timed_out_lanes(
        self,
    ) -> None:
        lines = read(TRANSCRIPT).splitlines()
        grep = lines.index("$ grep '\"status\"' out/*.json")
        self.assertEqual(lines[grep + 1 : grep + 5], STATUS_LINES)
        for name, budget in (("silent", "--idle-seconds 1"), ("endless", "--timeout-seconds 2")):
            command = [line for line in lines if line.startswith("$ review %s " % name)]
            self.assertEqual(len(command), 1, name)
            self.assertIn(budget, command[0])
            at = lines.index(command[0])
            self.assertEqual(lines[at + 1 : at + 3], ['$ echo "exit status: $?"', "exit status: 2"])

    def test_the_stalled_lane_markdown_is_shown(self) -> None:
        text = read(TRANSCRIPT)
        self.assertIn("- status: `stalled`\n- cwd: `/work/repo`\n", text)

    def test_readme_and_personas_name_the_opt_in_inputs(self) -> None:
        readme = read(README)
        personas = read(PACKAGE / "references" / "personas.md")
        for name in OPT_IN_INPUTS:
            self.assertIn("`%s`" % name, readme, name)
            self.assertIn("`%s`" % name, personas, name)
            self.assertIn("`%s`" % name, read(SKILL), name)

    def test_the_marker_keeps_its_wire_name(self) -> None:
        self.assertIn(MARKER, read(SKILL))
        self.assertIn(MARKER, read(PACKAGE / "references" / "write-back.md"))
        self.assertIn(MARKER, read(README))

    def test_readme_names_each_companion_skill_and_its_repository(self) -> None:
        text = " ".join(read(README).split())
        for name in ("l8", "address-comments"):
            self.assertIn("`trycopilotai/%s`" % name, text, name)
        self.assertIn("`not_run`", text)
        self.assertIn("`not_run`", read(PACKAGE / "references" / "personas.md"))

    def test_each_install_block_pins_the_manifest_version(self) -> None:
        version = manifest(".claude-plugin")["version"]
        blocks = install_blocks()
        self.assertEqual(len(blocks), 2)
        roots = []
        for block in blocks:
            self.assertEqual(
                re.findall(r"^release=(\S+)$", block, flags=re.M),
                ["v" + version],
            )
            self.assertIn(REPOSITORY + " \\\n", block)
            self.assertIn('--branch "$release"', block)
            target = re.findall(r'^install_target="\$HOME/(\S+)"$', block, flags=re.M)
            self.assertEqual(len(target), 1)
            roots.append(target[0])
        self.assertEqual(
            sorted(roots),
            [".agents/skills/" + NAME, ".claude/skills/" + NAME],
        )

    def test_relative_links_resolve(self) -> None:
        targets = re.findall(r"\]\(([^)#]+)\)", read(README))
        self.assertTrue(targets)
        for target in targets:
            if target.startswith("http"):
                continue
            self.assertTrue((ROOT / target).exists(), target)

    def test_readme_says_what_was_not_measured(self) -> None:
        text = " ".join(read(README).split())
        self.assertIn("No agent invoked the skill to produce the bounded session", text)
        self.assertIn("have not been measured", text)
        self.assertNotIn("No agent invocation is recorded in this release", text)

    def test_demo_is_offered_with_a_reduced_motion_poster(self) -> None:
        text = read(README)
        picture = re.search(r"<picture>(.*?)</picture>", text, flags=re.S)
        self.assertIsNotNone(picture)
        body = picture.group(1)
        self.assertIn('media="(prefers-reduced-motion: reduce)"', body)
        self.assertIn('srcset="assets/poster.svg"', body)
        self.assertIn('src="assets/demo.svg"', body)


class EvidenceTest(unittest.TestCase):
    def test_manifest_hashes_match_the_files(self) -> None:
        record = json.loads(read(MANIFEST))
        self.assertEqual(record["skill"]["sha256"], sha256(SKILL))
        programs = {item["path"]: item["sha256"] for item in record["programs"]}
        self.assertEqual(
            programs,
            {PROGRAM.relative_to(ROOT).as_posix(): sha256(PROGRAM)},
        )
        self.assertEqual(record["output"]["sha256"], sha256(TRANSCRIPT))
        self.assertIs(record["output"]["edited"], True)
        self.assertTrue(record["output"]["transforms"])
        self.assertIs(record["agent"]["invoked_the_skill"], False)

    def test_manifest_commands_are_the_ones_in_the_transcript(self) -> None:
        record = json.loads(read(MANIFEST))
        commands = [
            line[2:]
            for line in read(TRANSCRIPT).splitlines()
            if line.startswith("$ ") and not line.startswith("$ echo")
        ]
        self.assertEqual(record["invocation"]["commands"], commands)

    def test_readme_names_every_edit_the_manifest_declares(self) -> None:
        record = json.loads(read(MANIFEST))
        names = [entry["name"] for entry in record["output"]["transforms"]]
        self.assertEqual(names, ["replace-capture-root"])
        for name in names:
            self.assertIn("`%s`" % name, read(README))

    def test_the_stand_in_finding_has_the_persona_output_shape(self) -> None:
        finding = load(RECORDER, "record_session").FINDING
        shape = [
            "status: findings",
            "findings:",
            "- severity: P2",
            "  file: greet.py",
            "  line: 2",
            "reviewed_files:",
            "commands_run:",
            "confidence: high",
        ]
        for line in shape:
            self.assertIn(line + "\n", finding)
        for field in ("issue", "impact", "recommendation", "test"):
            self.assertIn("  %s: " % field, finding)
        skill = read(SKILL)
        for field in ("severity", "file", "line", "issue", "impact", "recommendation", "test"):
            self.assertIn("  %s: " % field, skill.replace("- severity", "  severity"))

    def test_transcript_carries_no_capture_path(self) -> None:
        text = read(TRANSCRIPT)
        self.assertIn("`/work/repo`", text)
        for fragment in ("/var/folders", "/private/", "/Users/", "/home/"):
            self.assertNotIn(fragment, text)


class InvocationTest(unittest.TestCase):
    def records(self) -> list:
        return json.loads(read(MANIFEST))["invocations"]

    def published(self) -> list:
        return [r for r in self.records() if r["published"]]

    def test_one_published_invocation_per_client(self) -> None:
        self.assertEqual(
            sorted(r["product"] for r in self.published()), ["Claude Code", "Codex"]
        )
        for record in self.records():
            self.assertIs(record["invoked_the_skill"], True)
            self.assertRegex(record["raw_output_sha256"], r"^[0-9a-f]{64}$")
            self.assertTrue(record["outcome"])
            for note in record.get("inaccuracies", []):
                self.assertTrue(note.strip())
            if not record["published"]:
                self.assertNotIn("transcript", record)
                self.assertTrue(record["published_note"])

    def test_published_transforms_are_declared_in_order(self) -> None:
        for record in self.published():
            transforms = [t for t in record["transforms"] if t != "replace-isolation-root"]
            self.assertEqual(transforms, INVOCATION_TRANSFORMS)
            if "replace-isolation-root" in record["transforms"]:
                self.assertEqual(record["transforms"][0], "replace-isolation-root")

    def test_invocation_text_matches_the_client(self) -> None:
        forms = {"Claude Code": "/" + NAME, "Codex": "$" + NAME}
        for record in self.records():
            self.assertEqual(record["invocation"], forms[record["product"]])
        for record in self.published():
            self.assertIn(record["invocation"], record["prompt"])

    def test_transcript_hashes_match_the_manifest(self) -> None:
        listed = set()
        for record in self.published():
            path = ROOT / record["transcript"]["path"]
            self.assertEqual(record["transcript"]["sha256"], sha256(path))
            listed.add(path.name)
            text = read(path)
            self.assertIn(record["prompt"], text)
            self.assertIn("\n## final message\n", text)
        on_disk = {p.name for p in TRANSCRIPT.parent.glob("*-invocation.txt")}
        self.assertEqual(listed, on_disk)

    def test_each_transcript_shows_the_l8_lane_not_run(self) -> None:
        for record in self.published():
            final = read(ROOT / record["transcript"]["path"]).split("\n## final message\n")[1]
            self.assertIn("not_run", final)
            self.assertIn("l8", final)

    def test_readme_links_each_transcript(self) -> None:
        text = read(README)
        for record in self.published():
            self.assertIn("](" + record["transcript"]["path"] + ")", text)

    def test_transcripts_name_only_the_replaced_roots(self) -> None:
        absolute = re.compile(r"(?<![\w.~/])/(?:private|tmp|var|home|Users)/")
        for path in TRANSCRIPT.parent.glob("*-invocation.txt"):
            self.assertEqual(absolute.findall(read(path)), [], path.name)


class RendererTest(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load(RENDERER, "render_invocation")
        self.transforms = self.module.Transforms(
            "/h/u/fix", ["/h/u/fix/.agents/skills/" + NAME, "/h/u/clone"], "/h/u", "box.local"
        )

    def test_transforms_replace_whole_prefixes_in_order(self) -> None:
        t = self.transforms
        self.assertEqual(t("/h/u/fix/.agents/skills/%s/SKILL.md" % NAME), "/plugin/SKILL.md")
        self.assertEqual(t("/h/u/clone/skills/x"), "/plugin/skills/x")
        self.assertEqual(t("cd /h/u/fix && ls"), "cd /work && ls")
        self.assertEqual(t("/h/u/fixture/a"), "~/fixture/a")
        self.assertEqual(t("/h/u2/a"), "/h/u2/a")
        self.assertEqual(t("/private/tmp/claude-0/-h-u-fix/t/out"), "/scratch/t/out")
        self.assertEqual(t("on box.local and box"), "on host and host")
        self.assertEqual(t("boxes"), "boxes")

    def test_isolation_root_is_replaced_first(self) -> None:
        t = self.module.Transforms(
            "/iso/fixture", ["/iso/plugin"], "/h/u", "box", ["/t/iso.1", "/private/t/iso.1"]
        )
        self.assertEqual(t("/private/t/iso.1/fixture/repo/a.py"), "/work/repo/a.py")
        self.assertEqual(t("/t/iso.1/plugin/skills/x"), "/plugin/skills/x")
        self.assertEqual(t("/t/iso.1/other"), "/iso/other")
        self.assertEqual(t("/t/iso.12/x"), "/t/iso.12/x")

    def test_transforms_reach_string_values_but_not_keys(self) -> None:
        event = {"/h/u/fix": ["/h/u/fix/a", {"k": "box.local"}], "n": 1}
        self.assertEqual(
            self.module.deep(event, self.transforms),
            {"/h/u/fix": ["/work/a", {"k": "host"}], "n": 1},
        )

    def test_claude_code_log(self) -> None:
        events = [
            {"type": "system", "subtype": "init", "claude_code_version": "9.9.9", "model": "m"},
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "a", "name": "Bash",
                 "input": {"command": "cat /h/u/fix/" + "x" * 500}}]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "a", "is_error": True,
                 "content": "Exit code 2\nboom"}]}},
            {"type": "result", "subtype": "success", "num_turns": 2,
             "duration_ms": 5, "total_cost_usd": 0.5, "result": "done\nok\n\n"},
        ]
        raw = "\n".join(json.dumps(e) for e in events)
        text = self.module.render("claude-code", "Use /" + NAME + "\n", raw, self.transforms)
        self.assertIn("model: m\n", text)
        self.assertIn("## prompt\n\nUse /" + NAME + "\n\n## tool calls", text)
        self.assertIn("    command: cat /work/" + "x" * 390 + " ...[", text)
        self.assertIn("  < error (exit 2)\n", text)
        self.assertTrue(text.endswith("## final message\n\ndone\nok\n"))

    def test_codex_log(self) -> None:
        events = [
            {"type": "thread.started", "thread_id": "t"},
            {"type": "item.completed", "item": {"type": "command_execution",
             "command": "python3 /h/u/fix/r.py", "exit_code": 2}},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "y" * 450}},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "done"}},
            {"type": "turn.completed", "usage": {"output_tokens": 3}},
        ]
        raw = "\n".join(json.dumps(e) for e in events)
        text = self.module.render("codex", "Use $" + NAME, raw, self.transforms)
        self.assertIn("    command: python3 /work/r.py\n  < exit 2\n", text)
        self.assertIn("usage output_tokens: 3\n", text)
        self.assertIn("agent: " + "y" * 400 + " ...[50 more characters]\n", text)
        self.assertTrue(text.endswith("## final message\n\ndone\n"))


class DemoTest(unittest.TestCase):
    def test_images_agree_with_the_transcript(self) -> None:
        verifier = load(ROOT / "scripts" / "verify_demo.py", "verify_demo")
        generator = verifier.load_generator()
        self.assertEqual(verifier.problems_in(generator, read(TRANSCRIPT)), [])


class SocialPreviewTest(unittest.TestCase):
    def test_preview_is_the_size_github_expects(self) -> None:
        header = (ROOT / "assets" / "social-preview.png").read_bytes()[:24]
        self.assertEqual(header[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(struct.unpack(">II", header[16:24]), (1280, 640))

    def test_stamp_binds_the_source_and_the_render(self) -> None:
        recorded = {}
        for line in read(ROOT / "assets" / "social-preview.sha256").splitlines():
            value, name = line.split()
            recorded[name] = value
        for name in ("social-preview.html", "social-preview.png"):
            self.assertEqual(recorded[name], sha256(ROOT / "assets" / name), name)

    def test_preview_source_carries_the_claim(self) -> None:
        text = " ".join(read(ROOT / "assets" / "social-preview.html").split())
        self.assertIn(CLAIM, text)

    def test_preview_shows_only_transcript_lines(self) -> None:
        html = read(ROOT / "assets" / "social-preview.html")
        shown = re.findall(r'<div class="output">(.*?)</div>', html)
        self.assertEqual(len(shown), 2)
        lines = read(TRANSCRIPT).splitlines()
        for line in shown:
            self.assertIn(line, lines)


class SupportFilesTest(unittest.TestCase):
    def test_license_is_mit(self) -> None:
        self.assertTrue(read(ROOT / "LICENSE").startswith("MIT License\n"))

    def test_security_names_this_repository_for_reports(self) -> None:
        self.assertIn(
            REPOSITORY + "/security/advisories/new",
            read(ROOT / "SECURITY.md"),
        )

    def test_contributing_names_the_check_command(self) -> None:
        self.assertIn("make check", read(ROOT / "CONTRIBUTING.md"))


if __name__ == "__main__":
    unittest.main()
