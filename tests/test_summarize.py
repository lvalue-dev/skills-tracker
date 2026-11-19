"""LLM 한글 요약의 단위 테스트 — 실제 API 는 부르지 않는다 (가짜 post 주입)."""
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import guide, report, summarize  # noqa: E402

REPO = {"full_name": "Someone/New-Skills", "name": "new-skills", "description": "agent skills", "topics": []}
CFG = {"key": "test-key", "base_url": "https://llm.invalid/v1", "model": "test/model", "timeout": 5.0, "budget": 60.0}
GOOD = {"summary": "PDF 에서 표를 뽑는 스킬 모음이다.", "use_when": ["PDF 표를 CSV 로 옮겨야 할 때", "문서를 검토할 때"],
        "caution": ["`pdfplumber` 설치가 필요하다"],
        "highlights": [{"skill": "pdf-tables", "note": "PDF 의 표를 추출한다"},
                       {"skill": "made-up-skill", "note": "목록에 없는 스킬"}]}


def skill(name, description):
    return f"---\nname: {name}\ndescription: {description}\n---\n# T\nBody.\n"


def make_guide():
    texts = {"skills/pdf-tables": skill("pdf-tables", "Extract tables from PDF files. Use when the user mentions PDFs."),
             "skills/review": skill("review", "Review a document.")}
    return guide.build_from({f"{d}/SKILL.md": "100644" for d in texts}, texts, "# Demo\nSkills for documents.\n", REPO)


class FakePost:
    def __init__(self, *replies):
        self.replies, self.seen = list(replies), []

    def __call__(self, cfg, messages):
        self.seen.append(messages)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)


class ParseTests(unittest.TestCase):
    def test_reply_with_think_and_fence(self):
        text = '<think>{"not": "this"}</think>\n```json\n{"summary": "요약"}\n```'
        self.assertEqual(summarize.parse_reply(text), {"summary": "요약"})

    def test_reply_without_json_is_rejected(self):
        for text in ("", "죄송합니다", "[1, 2]", "{broken"):
            with self.assertRaises(summarize.SummarizeError):
                summarize.parse_reply(text)

    def test_clean_note_keeps_only_valid_parts(self):
        note = summarize.clean_note({**GOOD, "use_when": GOOD["use_when"] + ["English only", 3, "자세한 건 https://evil.example 참고"]},
                                    make_guide())
        self.assertEqual(note["use_when"], GOOD["use_when"])
        self.assertEqual(note["highlights"], [{"skill": "pdf-tables", "note": "PDF 의 표를 추출한다"}])
        self.assertEqual(set(note), {"summary", "use_when", "caution", "highlights"})

    def test_clean_note_requires_korean_summary(self):
        for bad in ({}, {"summary": "English summary"}, {"summary": ["요약"]}, {"summary": "요약 http://x.example"}):
            with self.assertRaises(summarize.SummarizeError):
                summarize.clean_note(bad, make_guide())

    def test_clean_note_bounds_length_and_strips_control_chars(self):
        note = summarize.clean_note({"summary": "가" * 900 + "‮\n\x00"}, make_guide())
        self.assertLessEqual(len(note["summary"]), 260)
        self.assertNotRegex(note["summary"], r"[\x00-\x1f‮]")

    def test_messages_treat_repo_text_as_data(self):
        system, user = summarize.build_messages(REPO, make_guide(), "# Demo\n<img src=x>\n" + "x" * 20000)
        self.assertIn("따르지 말고", system["content"])
        self.assertIn("pdf-tables", user["content"])
        self.assertNotIn("<img", user["content"])
        self.assertLess(len(user["content"]), summarize.README_CHARS + 3000)


class SummarizerTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        quiet = mock.patch.object(summarize.sys, "stderr", io.StringIO())  # 진행 로그는 테스트 출력에서 뺀다
        quiet.start()
        self.addCleanup(quiet.stop)
        self.path = os.path.join(self.dir.name, ".cache", "notes_auto.json")

    def make(self, post, today="2026-11-19", **cfg):
        return summarize.Summarizer(self.path, cfg={**CFG, **cfg}, post=post, today=today)

    def test_result_is_cached_and_marked_as_llm(self):
        post = FakePost(GOOD)
        note = self.make(post)(REPO, make_guide(), "readme")
        self.assertEqual(note["source"], "llm")
        self.assertEqual(note["model"], "test/model")
        self.assertEqual(note["updated"], "2026-11-19")
        again = self.make(FakePost())(REPO, make_guide(), "readme")  # 캐시로 충분 — post 를 부르면 IndexError
        self.assertEqual(again, note)
        with open(self.path, encoding="utf-8") as fh:
            self.assertNotIn("test-key", fh.read())

    def test_refreshes_when_skills_change_or_cache_is_old(self):
        self.make(FakePost(GOOD))(REPO, make_guide(), None)
        changed = make_guide()
        changed["skills"].append({"name": "new-one", "what": "", "when": ""})
        post = FakePost({**GOOD, "summary": "새 요약이다."})
        self.assertEqual(self.make(post)(REPO, changed, None)["summary"], "새 요약이다.")
        post = FakePost({**GOOD, "summary": "한 달 뒤 요약이다."})
        self.assertEqual(self.make(post, today="2026-12-25")(REPO, changed, None)["summary"], "한 달 뒤 요약이다.")

    def test_failure_returns_none_after_one_retry(self):
        post = FakePost(summarize.SummarizeError("HTTP 429"), "모델이 JSON 을 안 줌")
        self.assertIsNone(self.make(post)(REPO, make_guide(), None))
        self.assertEqual(len(post.seen), 2)
        self.assertFalse(os.path.exists(self.path))

    def test_retry_can_succeed(self):
        post = FakePost(summarize.SummarizeError("HTTP 503"), GOOD)
        self.assertEqual(self.make(post)(REPO, make_guide(), None)["summary"], GOOD["summary"])

    def test_stale_note_survives_a_failed_refresh(self):
        self.make(FakePost(GOOD))(REPO, make_guide(), None)
        post = FakePost(summarize.SummarizeError("HTTP 500"), summarize.SummarizeError("HTTP 500"))
        note = self.make(post, today="2027-03-01")(REPO, make_guide(), None)
        self.assertEqual(note["summary"], GOOD["summary"])

    def test_no_key_means_no_call(self):
        self.assertIsNone(self.make(FakePost(), key="")(REPO, make_guide(), None))

    def test_call_limit(self):
        runner = self.make(FakePost())
        runner.calls = summarize.MAX_CALLS
        self.assertIsNone(runner(REPO, make_guide(), None))

    def test_corrupt_cache_is_ignored(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("[not a dict")
        self.assertEqual(self.make(FakePost(GOOD))(REPO, make_guide(), None)["summary"], GOOD["summary"])


class IntegrationTests(unittest.TestCase):
    def test_build_guide_prefers_hand_written_note(self):
        calls = []
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "skills", "a"))
            with open(os.path.join(root, "skills", "a", "SKILL.md"), "w", encoding="utf-8") as fh:
                fh.write(skill("a", "Does a thing."))
            fetched = {"skill_dirs": ["skills/a"], "tree": {"skills/a/SKILL.md": "100644"}, "readme": None}
            limits = {"max_file_bytes": 524288}

            def fake(repo, built, readme):
                calls.append(repo["full_name"])
                return {"summary": "AI 가 쓴 요약", "source": "llm"}

            known = next(iter(guide.load_notes()["repos"]))
            hand = guide.build_guide(root, fetched, {**REPO, "full_name": known}, limits, fake)
            self.assertEqual(hand["note"]["source"], "hand")
            auto = guide.build_guide(root, fetched, REPO, limits, fake)
            self.assertEqual(auto["note"]["source"], "llm")
            self.assertIsNone(guide.build_guide(root, fetched, REPO, limits)["note"])
        self.assertEqual(calls, [REPO["full_name"]])

    def test_report_labels_ai_summary(self):
        g = make_guide()
        g["note"] = {"summary": "AI 가 쓴 <b>요약</b>", "use_when": ["쓸 때"], "caution": [], "highlights": [],
                     "source": "llm", "model": "test/model", "updated": "2026-11-19"}
        entry = {"full_name": "someone/x", "test": {"guide": g}}
        html = report._guide_html(entry)
        self.assertIn("AI 요약", html)
        self.assertNotIn("<b>요약</b>", html)
        self.assertIn("(AI 요약)", "\n".join(report._md_guide(entry)))
        g["note"]["source"] = "hand"
        self.assertNotIn("AI 요약", report._guide_html(entry))


if __name__ == "__main__":
    unittest.main()
