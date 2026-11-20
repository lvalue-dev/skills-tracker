"""오늘의 요약·웹 리포트 테스트 (네트워크 불필요): python -m unittest discover -s tests"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import highlights, report  # noqa: E402
from tracker.__main__ import write_index  # noqa: E402


def _test(score, skills=5, errors=(), high=()):
    return {"score": score, "grade": "A" if score >= 90 else "C", "counts": {"skills": skills},
            "issue_summary": [{"level": "error", "kind": k, "count": 1, "example": f"{k}/SKILL.md"} for k in errors],
            "flag_summary": [{"rule": r, "severity": "high", "label": "전체 삭제", "count": 2, "path": p, "line": 3}
                             for r, p in high]}


def _entry(name, rank, test, prev_rank=None, is_new=False, delta=None):
    return {"full_name": name, "rank": rank, "stars": 1000 - rank, "description": f"{name} desc",
            "html_url": f"https://github.com/{name}", "prev_rank": prev_rank, "is_new": is_new,
            "stars_delta": delta, "test": test}


class HighlightTests(unittest.TestCase):
    def setUp(self):
        self.previous = {"date": "2026-01-01", "top": [
            {"full_name": "a/a", "rank": 1, "test": _test(90, errors=("YAML 파싱 실패",))},
            {"full_name": "b/b", "rank": 2, "test": _test(100, skills=3, high=(("rm", "x/SKILL.md"),))},
            {"full_name": "gone/gone", "rank": 3, "test": _test(100)}]}
        self.snapshot = {
            "date": "2026-01-02", "pool_size": 50,
            "top": [_entry("b/b", 1, _test(95, skills=4, errors=("링크",), high=(("rm", "x/SKILL.md"),)), 2, delta=30),
                    _entry("a/a", 2, _test(100), 1, delta=900),
                    _entry("n/n", 3, _test(100, high=(("rm", "y/SKILL.md"),)), None, True, None)],
            "rising": [{"full_name": "a/a", "stars": 998, "delta": 900, "description": "a"},
                       {"full_name": "z/z", "stars": 40, "delta": 500, "description": "zeta skill"}],
            "climbers": [{"full_name": "z/z", "stars": 40, "delta": 500, "rank": 20, "prev_rank": 26,
                          "description": "zeta skill"}],
            "excluded": [{"full_name": "t/t", "stars": 5000, "reason": "SKILL.md 없음"}],
        }

    def build(self):
        with mock.patch("tracker.guide.note_for", return_value=None):
            return highlights.build(self.snapshot, self.previous)

    def test_comparison_facts(self):
        h = self.build()
        self.assertEqual([(m["full_name"], m["prev_rank"], m["rank"]) for m in h["top"]["moved"]],
                         [("b/b", 2, 1), ("a/a", 1, 2)])
        self.assertEqual([n["full_name"] for n in h["top"]["entered"]], ["n/n"])
        self.assertEqual([x["full_name"] for x in h["top"]["left"]], ["gone/gone"])
        self.assertEqual(h["star_leader"], {"full_name": "a/a", "delta": 900})
        self.assertEqual(h["outside_leader"]["full_name"], "z/z")
        t = h["tests"]
        self.assertEqual([(c["full_name"], c["fixed"]) for c in t["improved"]], [("a/a", ["YAML 파싱 실패"])])
        self.assertEqual([(c["full_name"], c["added"]) for c in t["worsened"]], [("b/b", ["링크"])])
        self.assertEqual(t["skill_changes"], [{"full_name": "b/b", "prev": 3, "now": 4}])
        self.assertEqual([(f["full_name"], f["status"]) for f in t["high_flags"]], [("b/b", "same"), ("n/n", "unknown")])
        self.assertEqual((t["avg_prev"], t["avg"]), (96.7, 98.3))

    def test_sentences(self):
        md = "\n".join(report._md_highlights({**self.snapshot, "highlights": self.build()}))
        for expected in ("## 오늘의 요약 — 2026-01-01 대비", "**b/b** ▲1 (2위→1위)", "**a/a** ▼1 (1위→2위)",
                         "신규 진입 **n/n** (3위)", "밖으로 **gone/gone** (전일 3위)", "스타 증가 1위: **a/a** +900",
                         "**z/z**: ▲6 (26위→20위), ★ +500 — zeta skill", "순위 상승 (전체 후보 50개 기준)",
                         "평균 점수 96.7 → 98.3", "**a/a** 90 → 100 — YAML 파싱 실패 해결",
                         "**b/b** 100 → 95 — 링크 새로 발생", "스킬 수 변화: **b/b** 3 → 4",
                         "— 전일과 같은 위치", "제외: **t/t**"):
            self.assertIn(expected, md)
        # 순위 상승 목록에 이미 있는 레포는 'Top 10 밖 스타 증가 1위' 로 다시 쓰지 않는다
        self.assertNotIn("Top 10 밖 스타 증가 1위", md)

    def test_first_day(self):
        h = highlights.build({**self.snapshot, "climbers": []}, None)
        md = "\n".join(report._md_highlights({**self.snapshot, "highlights": h}))
        self.assertIn("첫 수집일이라 비교 대상이 없습니다", md)
        self.assertIn("규격 오류: **b/b**", md)
        self.assertNotIn("남은 규격 오류", md)
        self.assertNotIn("순위 상승", md)

    def test_untrusted_names_are_escaped_in_web(self):
        self.snapshot["climbers"][0]["full_name"] = 'x/"><script>alert(1)</script>'
        self.snapshot["climbers"][0]["description"] = "<img src=x onerror=alert(1)>"
        html = report._html_highlights({**self.snapshot, "highlights": self.build()})
        self.assertNotIn("<script>", html)
        self.assertNotIn("<img", html)
        self.assertIn('href="https://github.com/x/%22%3E%3Cscript%3Ealert%281%29%3C%2Fscript%3E"', html)

    def test_index_page(self):
        with tempfile.TemporaryDirectory() as reports, tempfile.TemporaryDirectory() as data:
            snap = {**self.snapshot, "highlights": self.build()}
            for d in ("2026-01-01", "2026-01-02"):
                open(os.path.join(reports, f"{d}.pdf"), "wb").close()
            with open(os.path.join(data, "2026-01-02.json"), "w", encoding="utf-8") as fh:
                json.dump(snap, fh)
            write_index(reports, data)
            with open(os.path.join(reports, "index.html"), encoding="utf-8") as fh:
                index = fh.read()
            with open(os.path.join(reports, "README.md"), encoding="utf-8") as fh:
                readme = fh.read()
        self.assertLess(index.index("2026-01-02.html"), index.index("2026-01-01.html"))
        self.assertIn("Top 순위 변동 2건 · 신규 진입 n/n · 상승 1위 z/z ▲6", index)
        self.assertIn("[2026-01-02.html](2026-01-02.html)", readme)


if __name__ == "__main__":
    unittest.main()
