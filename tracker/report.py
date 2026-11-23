"""스냅샷(JSON) → Markdown / HTML / PDF 리포트."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from html import escape
from urllib.parse import quote

from . import guide as guide_mod

ISSUE_KINDS = [
    ("깨진 상대 링크", "깨진 상대 링크"),
    ("frontmatter 없음", "frontmatter 없음"),
    ("YAML 파싱 실패", "frontmatter YAML 파싱 실패"),
    ("매핑이 아님", "frontmatter 형식 오류"),
    ("읽을 수 없음", "SKILL.md 읽기 불가"),
    ("name 필드 없음", "name 필드 없음"),
    ("폴더명", "name 과 폴더명 불일치"),
    ("description 필드 없음", "description 필드 없음"),
    ("description 이 비어", "description 비어 있음"),
    ("너무 짧음", "description 이 너무 짧음"),
    ("description 이 1024자 초과", "description 1024자 초과"),
    ("compatibility", "compatibility 500자 초과"),
    ("본문(지침)이 비어", "본문 비어 있음"),
    ("본문이", "본문 500줄 초과"),
    ("깨진 상대 링크", "깨진 상대 링크"),
    ("문법 오류", "스크립트 문법 오류"),
    ("파싱 불가", "스크립트 파싱 불가"),
    ("name", "name 형식 규격 위반"),
]
LEVEL_LABEL = {"error": "규격 오류", "warning": "권고", "script": "스크립트"}


def issue_kind(message: str) -> str:
    for needle, label in ISSUE_KINDS:
        if needle in message:
            return label
    return message[:40]


def summarize_issues(issues: list[dict], limit: int = 6) -> list[dict]:
    groups: dict[tuple, dict] = {}
    for issue in issues:
        key = (issue["level"], issue_kind(issue["message"]))
        group = groups.setdefault(key, {"level": key[0], "kind": key[1], "count": 0,
                                        "example": issue["path"]})
        group["count"] += 1
    order = {"error": 0, "script": 1, "warning": 2}
    ranked = sorted(groups.values(), key=lambda g: (order[g["level"]], -g["count"]))
    return ranked[:limit]


def summarize_flags(flags: list[dict]) -> list[dict]:
    groups: dict[str, dict] = {}
    for flag in flags:
        group = groups.setdefault(flag["rule"], {**flag, "count": 0})
        group["count"] += 1
    return sorted(groups.values(), key=lambda g: (g["severity"] != "high", -g["count"]))


def clip(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def fmt_int(n) -> str:
    return f"{n:,}"


def fmt_delta(delta) -> str:
    if delta is None:
        return "–"
    if delta == 0:
        return "0"
    return f"{delta:+,}"


def rank_change(entry: dict) -> str:
    """전일 대비 순위 변동 폭: ▲2(두 계단 상승) · ▼1 · –(변동 없음) · NEW(전일 목록에 없음)."""
    if entry.get("is_new"):
        return "NEW"
    prev = entry.get("prev_rank")
    if prev is None or entry.get("rank") is None:
        return "–"
    diff = prev - entry["rank"]
    return "–" if diff == 0 else (f"▲{diff}" if diff > 0 else f"▼{-diff}")


def rank_path(entry: dict) -> str:
    """순위가 바뀐 경우의 이동 경로 (예: 5위→3위). 변동이 없으면 빈 문자열."""
    prev, rank = entry.get("prev_rank"), entry.get("rank")
    if entry.get("is_new") or prev is None or rank is None or prev == rank:
        return ""
    return f"{prev}위→{rank}위"


def rank_change_text(entry: dict) -> str:
    path = rank_path(entry)
    return f"{rank_change(entry)} ({path})" if path else rank_change(entry)


def ratio(ok: int, total: int) -> str:
    if not total:
        return "해당 없음"
    return f"{fmt_int(ok)} / {fmt_int(total)} ({100 * ok / total:.0f}%)"


def overview(snapshot: dict) -> dict:
    top = snapshot["top"]
    skills = sum(e["test"]["counts"]["skills"] for e in top)
    scores = [e["test"]["score"] for e in top]
    high = sum(1 for e in top for f in e["test"]["flag_summary"] if f["severity"] == "high")
    return {"repos": len(top), "skills": skills,
            "avg": round(sum(scores) / len(scores), 1) if scores else 0,
            "grades": Counter(e["test"]["grade"] for e in top), "high_flag_kinds": high}


# ------------------------------------------------------------- 활용 가이드 --

def guide_of(entry: dict) -> dict | None:
    """스냅샷의 활용 가이드 (이 기능 이전에 만든 스냅샷에는 없다)."""
    return (entry.get("test") or {}).get("guide") or None


def has_guides(snapshot: dict) -> bool:
    return any(guide_of(e) for e in snapshot["top"])


def is_ai(note: dict) -> bool:
    return note.get("source") == "llm"


def auto_summary(g: dict) -> str:
    cats = ", ".join(f"{c['label']} {c['count']}" for c in g.get("categories", [])[:4])
    copies = f" (복사본·번역본 {fmt_int(g['copies'])}개 제외)" if g.get("copies") else ""
    return f"{g.get('kind', '스킬 레포')} · 고유 스킬 {fmt_int(g.get('unique_skills', 0))}개{copies} · 주요 분야: {cats or '분류 없음'}"


def how_text(cli: dict) -> str:
    """적용 방법 한 줄 (Markdown 표기)."""
    if cli.get("how_kind") == "section":
        return "README 의 설치 안내 참고"
    cmds = " → ".join(f"`{c}`" for c in cli.get("how", []))
    return ("(일반) " if cli.get("how_kind") == "generic" else "") + cmds


def portability_text(compat: dict) -> str:
    """전용 기능 사용 요약. hard = 다른 CLI 에서 동작이 달라질 수 있는 기능, 나머지는 무시되는 확장 키."""
    p = compat.get("portable") or {}
    if not p.get("total"):
        return ""
    parts = [f"고유 스킬 {fmt_int(p['total'])}개 중 {fmt_int(p['clean'])}개는 다른 CLI 에서 동작이 달라질 전용 기능을 쓰지 않음"]
    feats = compat.get("features", [])
    hard = ", ".join(f"`{f['key']}` ×{f['count']}" for f in feats if f.get("hard"))
    soft = ", ".join(f"`{f['key']}` ×{f['count']}" for f in feats if not f.get("hard"))
    if hard:
        parts.append(f"전용 기능: {hard}")
    if soft:
        parts.append(f"확장 키(지원하지 않는 CLI 는 무시): {soft}")
    return " · ".join(parts)


def _md_guide(e: dict) -> list[str]:
    g = guide_of(e)
    if not g:
        return []
    note, compat = g.get("note") or {}, g.get("compat") or {}
    if note.get("summary"):
        out = [f"**어떤 스킬인가**{' (AI 요약)' if is_ai(note) else ''} — {note['summary']}", ""]
    else:
        out = [f"**어떤 스킬인가** (자동 분석) — {auto_summary(g)}", ""]
    whens = note.get("use_when") or [w + " (자동 분류)" for w in g.get("situations", [])]
    if whens:
        out += ["**이럴 때 쓴다**", ""] + [f"- {w}" for w in whens] + [""]
    if note.get("caution"):
        out += ["**알아둘 점**", ""] + [f"- {w}" for w in note["caution"]] + [""]
    if note.get("highlights"):
        out += ["**주요 스킬**", ""] + [f"- `{h['skill']}` — {h['note']}" for h in note["highlights"]] + [""]
    else:
        shown = [s for s in g.get("skills", []) if s.get("what") or s.get("when")][:5]
        if shown:
            out += ["**주요 스킬** (원문 설명)", ""]
            for sk in shown:
                when = f" / 언제: {_md(clip(sk['when'], 150))}" if sk.get("when") else ""
                out.append(f"- `{sk['name']}` — {_md(clip(sk['what'], 130))}{when}")
            out.append("")
    if compat.get("clis"):
        out += [f"**CLI 별 적용** — 최적화 대상: **{compat.get('optimized_label', '–')}**", "",
                "| CLI | 수준 | 적용 방법 | 근거 |", "|---|---|---|---|"]
        for cli in compat["clis"]:
            level = cli["level"]
            evidence = ", ".join(cli.get("evidence", [])[:3] + cli.get("caveats", [])[:1]) or "표준 SKILL.md"
            out.append(f"| {cli['label']} | {guide_mod.LEVEL_MARK[level]} {guide_mod.LEVEL_LABEL[level]} | "
                       f"{_md(how_text(cli))} | {_md(evidence)} |")
        out.append("")
        extra = [x for x in (note.get("cli"), portability_text(compat)) if x]
        out += [f"- {x}" for x in extra] + ([""] if extra else [])
    return out


def _md_matrix(snapshot: dict) -> list[str]:
    if not has_guides(snapshot):
        return []
    facts = guide_mod.CLI_FACTS
    out = ["## CLI 호환성 한눈에", "",
           "● 전용 지원 · ○ 적용 가능 · △ 일부 제한 (판정 기준은 맨 아래 ‘방법과 한계’)", "",
           "| # | 레포 | " + " | ".join(f["short"] for f in facts) + " | 최적화 대상 |",
           "|--:|---|" + ":--:|" * len(facts) + "---|"]
    for e in snapshot["top"]:
        g = guide_of(e)
        if not g:
            continue
        levels = {c["id"]: c["level"] for c in g["compat"]["clis"]}
        cells = " | ".join(guide_mod.LEVEL_MARK.get(levels.get(f["id"]), "–") for f in facts)
        out.append(f"| {e['rank']} | {e['full_name']} | {cells} | {g['compat'].get('optimized_label', '–')} |")
    return out + [""]


def _md_methods() -> list[str]:
    out = ["- 활용 가이드: ‘어떤 스킬인가·이럴 때 쓴다’는 README·SKILL.md 를 읽고 정리한 요약"
           f"({guide_mod.load_notes().get('updated') or '날짜 미상'} 기준)이며, 요약이 없는 레포는 "
           "LLM 이 README·SKILL.md 설명을 요약한 ‘AI 요약’(사람이 검토하지 않음, LLM 설정이 있을 때만) "
           "또는 SKILL.md description 과 키워드 분류로 만든 자동 분석으로 표시함.",
           "- CLI 수준: " + " / ".join(f"{guide_mod.LEVEL_MARK[k]} {guide_mod.LEVEL_LABEL[k]} = {v}"
                                     for k, v in guide_mod.LEVEL_HELP.items()) + ".",
           "- 최적화 대상: 매니페스트·스킬 위치·README 설치 안내·전용 기능 사용을 점수화한 추정. 실제로 각 CLI 에서 "
           "실행해 본 결과가 아님. 다른 CLI 에서 안 되는 기능이라는 판단은 그 CLI 공식 문서에 해당 기능이 없다는 데서 온 추론임.",
           "- ‘(일반)’ 적용 방법은 `npx skills add`(vercel-labs/skills) 기준의 일반 절차로, 레포별로 검증하지 않음.", "",
           f"### CLI 별 스킬 위치 (공식 문서, {guide_mod.FACTS_VERIFIED} 확인)", "",
           "| CLI | 스킬을 읽는 위치 | 묶음(패키징) | 출처 |", "|---|---|---|---|"]
    out += [f"| {f['label']} | {f['where']} | {f['package']} | {f['source']} |" for f in guide_mod.CLI_FACTS]
    return out + [""]


# ---------------------------------------------------------- 오늘의 요약 --
# 문장은 (스타일, 글자) 조각 목록으로 만든다. Markdown·HTML 이 같은 문장을 각자 형식으로 그린다.
# 스타일: t 보통 · b 굵게(레포 이름) · up 상승 · down 하락 · new 신규 · code 경로 · muted 보조 설명

def _repo(name: str) -> tuple[str, str]:
    return ("b", name)


def highlight_sections(h: dict) -> list[tuple[str, list[list[tuple[str, str]]]]]:
    prev = h.get("previous_date")
    sections = []

    # Top 10
    top = h["top"]
    items = []
    if not prev:
        items.append([("muted", "첫 수집일이라 비교 대상이 없습니다. 다음 리포트부터 전일 대비 변화가 나옵니다.")])
    elif not top["moved"] and not top["entered"]:
        items.append([("t", "순위 변동 없음")])
    else:
        for m in top["moved"]:
            diff = m["prev_rank"] - m["rank"]
            items.append([_repo(m["full_name"]), ("t", " "),
                          ("up" if diff > 0 else "down", f"{'▲' if diff > 0 else '▼'}{abs(diff)}"),
                          ("t", f" ({m['prev_rank']}위→{m['rank']}위)")])
    for n in top["entered"]:
        items.append([("new", "신규 진입"), ("t", " "), _repo(n["full_name"]), ("t", f" ({n['rank']}위)")])
    for x in top["left"]:
        items.append([("down", "밖으로"), ("t", " "), _repo(x["full_name"]), ("t", f" (전일 {x['prev_rank']}위)")])
    if prev and not top["entered"]:
        items.append([("t", "신규 진입 없음")])
    if h.get("star_leader"):
        sl = h["star_leader"]
        items.append([("t", "스타 증가 1위: "), _repo(sl["full_name"]), ("t", " "), ("up", fmt_delta(sl["delta"]))])
    sections.append((f"Top {h['tests']['repos']}", items))

    # 순위 상승
    if prev:
        items = []
        for c in h["climbers"]:
            diff = c["prev_rank"] - c["rank"]
            line = [_repo(c["full_name"]), ("t", ": "), ("up", f"▲{diff}"),
                    ("t", f" ({c['prev_rank']}위→{c['rank']}위), ★ "), ("up", fmt_delta(c["delta"]))]
            if c.get("blurb"):
                line.append(("muted", " — " + clip(c["blurb"], 90)))
            items.append(line)
        if h["climbers_total"] > len(h["climbers"]):
            items.append([("muted", f"그 밖에 {h['climbers_total'] - len(h['climbers'])}개 레포가 순위가 올랐습니다.")])
        if not items:
            items.append([("t", "순위가 오른 레포 없음")])
        ol = h.get("outside_leader")
        if ol and ol["full_name"] not in {c["full_name"] for c in h["climbers"]}:
            line = [("t", "Top 10 밖 스타 증가 1위: "), _repo(ol["full_name"]), ("t", " "), ("up", fmt_delta(ol["delta"])),
                    ("t", f" (★{fmt_int(ol['stars'])})")]
            if ol.get("blurb"):
                line.append(("muted", " — " + clip(ol["blurb"], 90)))
            items.append(line)
        sections.append((f"순위 상승 (전체 후보 {fmt_int(h.get('pool_size') or 0)}개 기준)", items))

    # 관심 레포
    if h.get("watch"):
        items = []
        for w in h["watch"]:
            if w.get("in_top"):
                items.append([_repo(w["full_name"]), ("t", f" — 오늘 Top {w['in_top']}위에 포함 (위 표 참고)")])
                continue
            if w.get("error"):
                items.append([_repo(w["full_name"]), ("down", f" — {w['error']}")])
                continue
            line = [_repo(w["full_name"]), ("t", f" ★{fmt_int(w['stars'])}")]
            if w.get("delta") is not None:
                line += [("t", " ("), ("up" if w["delta"] > 0 else "down" if w["delta"] < 0 else "t", fmt_delta(w["delta"])),
                         ("t", ")")]
            line.append(("t", f" · 스킬 {fmt_int(w['skills'])}개 · {w['score']}점 {w['grade']}"))
            if w.get("errors"):
                line.append(("down", f" · 규격 오류 {w['errors']}건"))
            if w.get("high"):
                line.append(("down", f" · 안전 신호(높음) {w['high']}건"))
            if w.get("new"):
                line.append(("new", " · 오늘부터 추적"))
            items.append(line)
        sections.append(("관심 레포", items))

    # 테스트 결과
    t = h["tests"]
    items = []
    grades = ", ".join(f"{g} {t['grades'][g]}개" for g in "ABCD" if t["grades"].get(g))
    all_one = len(t["grades"]) == 1
    grade_text = f"{t['repos']}개 모두 {next(iter(t['grades']))}등급" if all_one else f"등급 {grades}"
    if t.get("avg_prev") is not None and t["avg_prev"] != t["avg"]:
        items.append([("t", f"평균 점수 {t['avg_prev']} → "), ("up" if t["avg"] > t["avg_prev"] else "down", str(t["avg"])),
                      ("t", f", {grade_text}")])
    else:
        items.append([("t", f"평균 점수 {t['avg']}{' (전일과 같음)' if t.get('avg_prev') is not None else ''}, {grade_text}")])
    for c in t["improved"]:
        why = f" — {', '.join(c['fixed'])} 해결" if c["fixed"] else ""
        items.append([_repo(c["full_name"]), ("t", f" {c['prev']} → "), ("up", str(c["now"])), ("t", why)])
    for c in t["worsened"]:
        why = f" — {', '.join(c['added'])} 새로 발생" if c["added"] else ""
        items.append([_repo(c["full_name"]), ("t", f" {c['prev']} → "), ("down", str(c["now"])), ("t", why)])
    if t["errors"]:
        for err in t["errors"]:
            label = ("남은 규격 오류: " if prev else "규격 오류: ") if err["level"] == "error" else "스크립트 오류: "
            items.append([("t", label), _repo(err["full_name"]),
                          ("t", f" — {err['kind']} {err['count']}건 "), ("code", err["example"])])
    else:
        items.append([("t", "규격 오류 없음")])
    for c in t["skill_changes"]:
        items.append([("t", "스킬 수 변화: "), _repo(c["full_name"]), ("t", f" {fmt_int(c['prev'])} → {fmt_int(c['now'])}")])
    for f in t["high_flags"]:
        status = {"same": " — 전일과 같은 위치", "new": " — 새로 생김, 확인 필요", "unknown": ""}[f["status"]]
        items.append([("t", "안전 신호(높음): "), _repo(f["full_name"]), ("t", f" {f['label']} {f['count']}건"),
                      ("down" if f["status"] == "new" else "muted", status)])
    for x in h.get("excluded", []):
        items.append([("t", "제외: "), _repo(x["full_name"]), ("muted", f" — {x['reason']}")])
    sections.append(("테스트 결과", items))
    return sections


def _md_segments(line: list[tuple[str, str]]) -> str:
    out = []
    for style, text in line:
        text = _md(text)
        out.append(f"**{text}**" if style == "b" else f"`{text}`" if style == "code" else text)
    return "".join(out)


def _html_segments(line: list[tuple[str, str]]) -> str:
    out = []
    for style, text in line:
        if not text:
            continue
        if style == "b":
            out.append(repo_link(text))
        elif style == "code":
            out.append(f"<code>{escape(text)}</code>")
        elif style in ("up", "down", "new", "muted"):
            out.append(f'<span class="{ "none" if style == "muted" else style }">{escape(text)}</span>')
        else:
            out.append(escape(text))
    return "".join(out)


def _md_highlights(snapshot: dict) -> list[str]:
    h = snapshot.get("highlights")
    if not h:
        return []
    title = f"## 오늘의 요약 — {h['previous_date']} 대비" if h.get("previous_date") else "## 오늘의 요약"
    out = [title, ""]
    for heading, items in highlight_sections(h):
        out += [f"**{heading}**", ""] + [f"- {_md_segments(line)}" for line in items] + [""]
    return out


def _html_highlights(snapshot: dict) -> str:
    h = snapshot.get("highlights")
    if not h:
        return ""
    title = f"오늘의 요약 — {escape(h['previous_date'])} 대비" if h.get("previous_date") else "오늘의 요약"
    blocks = "".join(f'<div class="hl"><h3>{escape(heading)}</h3><ul>'
                     + "".join(f"<li>{_html_segments(line)}</li>" for line in items) + "</ul></div>"
                     for heading, items in highlight_sections(h))
    return f'<h2>{title}</h2><div class="hls">{blocks}</div>'


def repo_link(full_name: str) -> str:
    url = "https://github.com/" + "/".join(quote(part, safe="-_.") for part in full_name.split("/", 1))
    return f'<a class="rl" href="{escape(url, quote=True)}">{escape(full_name)}</a>'


RANK_NOTE = ("순위는 그날 검색된 후보 풀 전체를 스타 순으로 줄 세운 값입니다. Top 표의 순위는 그중 "
             "SKILL.md 를 가진 레포만 센 것이라 서로 다를 수 있습니다.")


# ---------------------------------------------------------------- Markdown --

def _md(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ").replace("<", "&lt;")


def watch_tested(snapshot: dict) -> list[dict]:
    """관심 레포 중 Top 에 없고 검사까지 마친 것."""
    return [w for w in snapshot.get("watch", []) if w.get("test") and not w.get("in_top")]


def _md_watch_table(snapshot: dict) -> list[str]:
    watch = snapshot.get("watch") or []
    if not watch:
        return []
    out = ["## 관심 레포", "", "순위와 상관없이 매일 따라가는 레포입니다 (`config.json` 의 `watchlist`).", "",
           "| 레포 | ★ 스타 | 전일 대비 | 스킬 수 | 점수 | 등급 | 추가 이유 |", "|---|--:|--:|--:|--:|:--:|---|"]
    for w in watch:
        link = f"[{w['full_name']}](https://github.com/{w['full_name']})"
        if w.get("in_top"):
            out.append(f"| {link} | – | – | – | – | – | 오늘 Top {w['in_top']}위 · {_md(w.get('why', ''))} |")
        elif not w.get("test"):
            out.append(f"| {link} | {fmt_int(w['stars']) if w.get('stars') else '–'} | – | – | – | – | "
                       f"{_md(w.get('error', ''))} |")
        else:
            t = w["test"]
            out.append(f"| {link} | {fmt_int(w['stars'])} | {fmt_delta(w.get('stars_delta'))} | "
                       f"{fmt_int(t['counts']['skills'])} | {t['score']} | {t['grade']} | {_md(w.get('why', ''))} |")
    return out + [""]


def _md_repo_block(e: dict) -> list[str]:
    t, c = e["test"], e["test"]["counts"]
    label = e.get("rank_label") or f"{e['rank']}."
    out = [f"### {label} {e['full_name']} — {t['grade']} ({t['score']}점)", "",
           f"> {_md(e['description']) or '(설명 없음)'}", ""]
    out += _md_guide(e)
    out += ([f"**테스트 결과**", ""] if guide_of(e) else [])
    out += [f"- ★ {fmt_int(e['stars'])} · 포크 {fmt_int(e['forks'])} · 라이선스 {e.get('license') or '미표기'}"
            f" · 최근 푸시 {(e.get('pushed_at') or '')[:10]} · 검사 커밋 `{(t.get('commit') or '')[:7]}`",
            f"- 규격 통과 {ratio(c['spec_pass'], c['skills'])} · 권고까지 무결 {ratio(c['clean'], c['skills'])}",
            f"- 상대 링크 정상 {ratio(c['links_total'] - c['links_broken'], c['links_total'])}"
            f" · 스크립트 문법 정상 {ratio(c['scripts_total'] - c['scripts_bad'], c['scripts_total'])}"]
    for g in t["issue_summary"]:
        out.append(f"- [{LEVEL_LABEL[g['level']]}] {g['kind']} × {g['count']} (예: `{g['example']}`)")
    for f in t["flag_summary"]:
        out.append(f"- [안전 신호·{f['severity']}] {f['label']} × {f['count']} (예: `{f['path']}:{f['line']}`)")
    return out + [""]


def _html_watch_table(snapshot: dict) -> str:
    watch = snapshot.get("watch") or []
    if not watch:
        return ""
    rows = []
    for w in watch:
        name = f'<div class="repo">{repo_link(w["full_name"])}</div><div class="desc">{escape(w.get("why", ""))}</div>'
        if w.get("in_top"):
            rows.append(f'<tr><td>{name}</td><td colspan="5" class="none">오늘 Top {w["in_top"]}위에 포함 — 위 표 참고</td></tr>')
        elif not w.get("test"):
            rows.append(f'<tr><td>{name}</td><td colspan="5" class="down">{escape(w.get("error", ""))}</td></tr>')
        else:
            t = w["test"]
            rows.append(f'<tr><td>{name}</td><td class="num">{fmt_int(w["stars"])}</td>'
                        f'<td class="num">{_delta_html(w.get("stars_delta"))}</td>'
                        f'<td class="num">{fmt_int(t["counts"]["skills"])}</td><td class="num">{t["score"]}</td>'
                        f'<td class="ctr"><span class="grade g{t["grade"]}">{t["grade"]}</span></td></tr>')
    return ('<h2>관심 레포</h2><p class="note">순위와 상관없이 매일 따라가는 레포입니다 (config.json 의 watchlist).</p>'
            '<table><thead><tr><th>레포 · 추가 이유</th><th class="num">★ 스타</th><th class="num">전일 대비</th>'
            '<th class="num">스킬 수</th><th class="num">점수</th><th class="ctr">등급</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table>')


def build_markdown(snapshot: dict) -> str:
    ov = overview(snapshot)
    out = [f"# AI Skills 일일 리포트 — {snapshot['date']}", "",
           f"- 생성: {snapshot['generated_at']} ({snapshot['timezone']})",
           f"- 후보 풀 {fmt_int(snapshot['pool_size'])}개 레포 중 SKILL.md 를 실제로 가진 "
           f"스타 상위 {ov['repos']}개를 테스트 (스킬 {fmt_int(ov['skills'])}개, 평균 {ov['avg']}점)",
           f"- PDF: [{snapshot['date']}.pdf]({snapshot['date']}.pdf) · 웹: [{snapshot['date']}.html]({snapshot['date']}.html)", ""]
    out += _md_highlights(snapshot)
    out += ["## 스타 상위 Top " + str(ov["repos"]), "",
           "| # | 순위 변동 | 레포 | ★ 스타 | 전일 대비 | 스킬 수 | 점수 | 등급 |",
           "|--:|:--:|---|--:|--:|--:|--:|:--:|"]
    for e in snapshot["top"]:
        t = e["test"]
        out.append(f"| {e['rank']} | {rank_change_text(e)} | [{e['full_name']}]({e['html_url']}) | "
                   f"{fmt_int(e['stars'])} | {fmt_delta(e.get('stars_delta'))} | "
                   f"{fmt_int(t['counts']['skills'])} | {t['score']} | {t['grade']} |")
    out.append("")
    out += _md_watch_table(snapshot)
    out += _md_matrix(snapshot)
    if snapshot.get("rising"):
        out += ["## 급상승 (전일 대비 스타 증가)", "",
                "| 레포 | 순위 | 순위 변동 | ★ 스타 | 증가 | 설명 |", "|---|--:|:--:|--:|--:|---|"]
        for r in snapshot["rising"]:
            out.append(f"| [{r['full_name']}](https://github.com/{r['full_name']}) | {r.get('rank') or '–'} | "
                       f"{rank_change_text(r)} | {fmt_int(r['stars'])} | {fmt_delta(r['delta'])} | "
                       f"{_md(clip(r['description'], 90))} |")
        out.append("")
    if snapshot.get("previous_date"):
        out += ["## 순위 상승 (전일 대비)", ""]
        if snapshot.get("climbers"):
            out += ["| 레포 | 상승 폭 | 순위 | ★ 스타 | 증가 |", "|---|:--:|:--:|--:|--:|"]
            out += [f"| [{c['full_name']}](https://github.com/{c['full_name']}) | {rank_change(c)} | "
                    f"{rank_path(c)} | {fmt_int(c['stars'])} | {fmt_delta(c['delta'])} |"
                    for c in snapshot["climbers"]]
        else:
            out.append(f"{snapshot['previous_date']} 대비 순위가 오른 레포가 없습니다.")
        out += ["", RANK_NOTE, ""]
    out += ["## 레포별 활용 가이드와 테스트 결과" if has_guides(snapshot) else "## 레포별 테스트 결과", ""]
    for e in snapshot["top"]:
        out += _md_repo_block(e)
    watched = watch_tested(snapshot)
    if watched:
        out += ["## 관심 레포 활용 가이드와 테스트 결과", ""]
        for e in watched:
            out += _md_repo_block(e)
    if snapshot.get("excluded"):
        out += ["## 제외된 후보", "", "| 레포 | ★ 스타 | 사유 |", "|---|--:|---|"]
        for x in snapshot["excluded"]:
            out.append(f"| {x['full_name']} | {fmt_int(x['stars'])} | {_md(x['reason'])} |")
        out.append("")
    out += ["## 방법과 한계", "",
            "- 수집: GitHub 검색 API(GET)만 사용, 스타 내림차순. 다른 레포에 대한 쓰기 동작(star·fork·issue·PR·댓글)은 없음.",
            "- 테스트: 얕은 스파스 클론 후 정적 검사만 수행. 내려받은 코드는 실행하지 않음.",
            "- 점수(규격 55 + 품질 권고 15 + 링크 15 + 스크립트 15)는 규격 준수·무결성을 잰 것으로, 스킬의 실제 효용을 보증하지 않음.",
            "- 안전 신호는 사람이 확인할 위치를 알려주는 표시이며 악성 판정이 아님. 오탐이 섞이므로 점수에 반영하지 않음."]
    out += _md_methods() if has_guides(snapshot) else [""]
    return "\n".join(out)


# -------------------------------------------------------------------- HTML --

CSS = """
@page { size: A4; margin: 15mm 14mm 16mm; @bottom-center { content: counter(page) " / " counter(pages);
  font-size: 8pt; color: #7a8791; font-family: "Noto Sans CJK KR", "Noto Sans KR", sans-serif; } }
* { box-sizing: border-box; }
html { font-family: "Noto Sans CJK KR", "Noto Sans KR", "Apple SD Gothic Neo", "Malgun Gothic", sans-serif;
  font-size: 9.2pt; line-height: 1.5; color: #1b2733; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { margin: 0; }
h1 { font-size: 21pt; line-height: 1.2; margin: 0 0 2pt; letter-spacing: -0.02em; }
h2 { font-size: 12.5pt; margin: 20pt 0 7pt; padding-bottom: 4pt; border-bottom: 1.5pt solid #1b2733;
  break-after: avoid; }
.kicker { font-size: 8pt; letter-spacing: 0.14em; color: #0b6e69; font-weight: 700; }
.sub { color: #5a6873; font-size: 8.6pt; margin-bottom: 12pt; }
.tiles { display: grid; grid-template-columns: repeat(4, 1fr); gap: 7pt; margin: 10pt 0 4pt; }
.tile { border: 0.75pt solid #d5dce1; border-radius: 5pt; padding: 8pt 10pt; }
.tile b { display: block; font-size: 16pt; line-height: 1.15; font-variant-numeric: tabular-nums; }
.tile span { color: #5a6873; font-size: 7.8pt; }
table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
th { text-align: left; font-size: 7.6pt; color: #5a6873; font-weight: 700; padding: 4pt 5pt;
  border-bottom: 0.75pt solid #9aa6af; white-space: nowrap; }
td { padding: 5pt 5pt; border-bottom: 0.5pt solid #e1e6ea; vertical-align: top; }
tr { break-inside: avoid; }
.num { text-align: right; white-space: nowrap; }
.ctr { text-align: center; white-space: nowrap; }
.repo { font-weight: 700; overflow-wrap: anywhere; }
.desc { color: #5a6873; font-size: 7.8pt; overflow-wrap: anywhere; }
.up { color: #0b6e69; font-weight: 700; } .down { color: #b3402a; font-weight: 700; }
.new { color: #8a5a00; font-weight: 700; font-size: 7.4pt; }
.path { color: #5a6873; font-size: 7pt; line-height: 1.3; }
.grade { display: inline-block; min-width: 15pt; text-align: center; border-radius: 3pt; padding: 0 4pt;
  font-weight: 700; color: #fff; }
.gA { background: #0b6e69; } .gB { background: #3a6ea5; } .gC { background: #a8730a; } .gD { background: #b3402a; }
.card { border: 0.75pt solid #d5dce1; border-radius: 6pt; padding: 9pt 12pt; margin: 0 0 8pt; break-inside: avoid; }
.card h3 { margin: 0; font-size: 11pt; display: flex; gap: 7pt; align-items: baseline; }
.card h3 .rk { color: #7a8791; font-weight: 400; }
.card h3 .sc { margin-left: auto; font-size: 9pt; font-weight: 400; color: #5a6873; white-space: nowrap; }
.meta { color: #5a6873; font-size: 7.8pt; margin: 2pt 0 5pt; }
.cdesc { margin: 0 0 7pt; overflow-wrap: anywhere; }
.checks { display: grid; grid-template-columns: repeat(5, 1fr); gap: 5pt; margin-bottom: 7pt; }
.check { background: #f3f6f8; border-radius: 4pt; padding: 5pt 7pt; }
.check b { display: block; font-size: 9.4pt; font-variant-numeric: tabular-nums; }
.check span { font-size: 7.2pt; color: #5a6873; }
.cols { display: grid; grid-template-columns: 1fr 1fr; gap: 12pt; }
.cols h4 { margin: 0 0 3pt; font-size: 7.8pt; color: #5a6873; letter-spacing: 0.04em; }
ul { margin: 0; padding-left: 11pt; } li { margin-bottom: 2pt; overflow-wrap: anywhere; }
.tag { font-size: 7pt; font-weight: 700; border-radius: 2.5pt; padding: 0.5pt 3.5pt; margin-right: 3pt; white-space: nowrap; }
.t-error { background: #f6ddd7; color: #8f2c1a; } .t-script { background: #f4e7c8; color: #6f4a00; }
.t-warning { background: #e4e9ed; color: #44525d; } .t-high { background: #f6ddd7; color: #8f2c1a; }
.t-medium { background: #f4e7c8; color: #6f4a00; }
code { font-family: "DejaVu Sans Mono", "Noto Sans Mono CJK KR", monospace; font-size: 7.4pt; color: #44525d; }
.none { color: #7a8791; }
.note { color: #44525d; font-size: 8.4pt; } .note li { margin-bottom: 3pt; }
.card.long { break-inside: auto; } .card.long h3, .card h4 { break-after: avoid; }
.blk { break-inside: avoid; } .clit tr, .matrix tr { break-inside: avoid; }
.sect { break-inside: avoid; }
.guide { border-top: 0.5pt solid #e1e6ea; border-bottom: 0.5pt solid #e1e6ea; padding: 7pt 0 6pt; margin: 0 0 8pt; }
.gsum { margin: 0 0 6pt; font-size: 9.6pt; }
.t-ko { background: #d9eeec; color: #0b5551; } .t-auto { background: #e4e9ed; color: #44525d; }
.t-ai { background: #ece6f6; color: #4b3a7a; }
.guide .cols { margin-bottom: 6pt; }
.card h4 { margin: 0 0 3pt; font-size: 7.8pt; color: #5a6873; letter-spacing: 0.04em; }
.card h4 b { color: #1b2733; font-size: 8.6pt; letter-spacing: 0; }
.lv { display: inline-block; font-size: 7.2pt; font-weight: 700; border-radius: 2.5pt; padding: 0.5pt 4pt; white-space: nowrap; }
.lv-native { background: #0b6e69; color: #fff; } .lv-ok { background: #e4e9ed; color: #1b2733; }
.lv-limited { background: #f4e7c8; color: #6f4a00; }
.clit { font-size: 7.8pt; } .clit td { padding: 2.5pt 5pt; } .clit td:first-child { white-space: nowrap; font-weight: 700; }
.clit .ev { color: #5a6873; }
.matrix { table-layout: fixed; } .matrix th:first-child { width: 4%; } .matrix th:nth-child(2) { width: 28%; }
.matrix th:last-child { width: 21%; } .matrix td { padding-top: 4pt; padding-bottom: 4pt; }
.matrix td, .matrix th { text-align: center; } .matrix td:nth-child(2), .matrix th:nth-child(2),
.matrix td:last-child, .matrix th:last-child { text-align: left; }
.legend { color: #5a6873; font-size: 7.8pt; margin: 5pt 0 0; } .legend .lv { margin: 0 3pt 0 8pt; }
.legend .lv:first-child { margin-left: 0; }
.facts { font-size: 7.8pt; margin-top: 6pt; } .facts td:first-child { white-space: nowrap; font-weight: 700; }
a.rl { color: inherit; text-decoration: none; }
.hls { display: grid; grid-template-columns: 1fr 1fr; gap: 6pt 14pt; }
.hl { break-inside: avoid; } .hl:last-child { grid-column: 1 / -1; }
.hl h3 { font-size: 9.6pt; margin: 2pt 0 3pt; color: #0b6e69; }
.hl ul { padding-left: 12pt; } .hl li { margin-bottom: 2.5pt; }
@media screen {
  html { background: #eef1f3; font-size: 10.5pt; }
  body { max-width: 980px; margin: 0 auto; padding: 28px 32px 56px; background: #ffffff; }
  a { color: #0b6e69; }
  a.rl { color: inherit; }
  a.rl:hover { text-decoration: underline; color: #0b6e69; }
  .navbar { font-size: 9pt; margin-bottom: 10pt; } .navbar a { color: #0b6e69; }
}
@media screen and (max-width: 680px) {
  body { padding: 16px 16px 40px; }
  .tiles, .checks { grid-template-columns: repeat(2, 1fr); }
  .cols, .hls { grid-template-columns: 1fr; }
  table { display: block; overflow-x: auto; }
  .repo { min-width: 170px; }
  .hl .repo, .card .repo { min-width: 0; }
}
@media print { .navbar { display: none; } }
"""


def _inline(text: str) -> str:
    """escape 후 `백틱` 구간만 <code> 로 바꾼다."""
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", escape(text or ""))


def _lv(level: str, short: bool = False) -> str:
    label = {"native": "전용", "ok": "가능", "limited": "제한"}[level] if short else guide_mod.LEVEL_LABEL[level]
    return f'<span class="lv lv-{level}">{guide_mod.LEVEL_MARK[level]} {label}</span>'


def _guide_html(e: dict) -> str:
    g = guide_of(e)
    if not g:
        return ""
    note, compat = g.get("note") or {}, g.get("compat") or {}
    if note.get("summary"):
        tag = '<span class="tag t-ai">AI 요약</span>' if is_ai(note) else '<span class="tag t-ko">요약</span>'
        summary = tag + _inline(note["summary"])
    else:
        summary = f'<span class="tag t-auto">자동 분석</span>{escape(auto_summary(g))}'
    whens = note.get("use_when") or g.get("situations", [])
    auto = "" if note.get("use_when") else ' <span class="tag t-auto">자동 분류</span>'
    left = (f'<div><h4>이럴 때 쓴다{auto}</h4><ul>' + "".join(f"<li>{_inline(w)}</li>" for w in whens)
            + "</ul></div>") if whens else "<div></div>"
    if note.get("caution"):
        right = "<div><h4>알아둘 점</h4><ul>" + "".join(f"<li>{_inline(w)}</li>" for w in note["caution"]) + "</ul></div>"
    else:
        cats = "".join(f"<li>{escape(c['label'])} <span class='none'>{c['count']}개</span></li>"
                       for c in g.get("categories", [])[:5])
        right = f"<div><h4>분야별 스킬 수</h4><ul>{cats}</ul></div>"
    rows = ""
    for cli in compat.get("clis", []):
        if cli.get("how_kind") == "section":
            how = '<span class="ev">README 의 설치 안내 참고</span>'
        else:
            how = "<br>".join(f"<code>{escape(c)}</code>" for c in cli.get("how", []))
            if cli.get("how_kind") == "generic":
                how = '<span class="tag t-auto">일반</span>' + how
        evidence = ", ".join(cli.get("evidence", [])[:3] + cli.get("caveats", [])[:1]) or "표준 SKILL.md"
        rows += (f'<tr><td>{escape(cli["label"])}</td><td>{_lv(cli["level"])}</td><td>{how}</td>'
                 f'<td class="ev">{_inline(evidence)}</td></tr>')
    extra = " · ".join(x for x in (note.get("cli"), portability_text(compat)) if x)
    table = ""
    if rows:
        table = (f'<div><h4>CLI 별 적용 — 최적화 대상 <b>{escape(compat.get("optimized_label", "–"))}</b></h4>'
                 f'<table class="clit"><tbody>{rows}</tbody></table>'
                 + (f'<p class="desc" style="margin:4pt 0 0">{_inline(extra)}</p>' if extra else "") + "</div>")
    return (f'<div class="guide"><div class="blk"><p class="gsum">{summary}</p>'
            f'<div class="cols">{left}{right}</div></div>{table}</div>')


def _skills_html(e: dict) -> tuple[str, str]:
    """(제목, <li>…) — 활용 가이드가 있으면 무엇/언제 중심으로 보여 준다."""
    t, c = e["test"], e["test"]["counts"]
    g = guide_of(e)
    if not g:
        items = "".join(
            f'<li><b>{escape(clip(s["name"], 48))}</b> <span class="desc">{escape(clip(s["description"], 88))}'
            f'</span></li>' for s in t["skills"][:4])
        more = c["skills"] - min(len(t["skills"]), 4)
        return "수록 스킬 예시", items + (f'<li class="none">외 {fmt_int(more)}개</li>' if more > 0 else "")
    note = g.get("note") or {}
    if note.get("highlights"):
        shown = note["highlights"][:6]
        items = "".join(f'<li><b>{escape(h["skill"])}</b> — {_inline(h["note"])}</li>' for h in shown)
        title = "주요 스킬"
    else:
        shown = [s for s in g.get("skills", []) if s.get("what") or s.get("when")][:4]
        items = ""
        for sk in shown:
            cat = guide_mod.CAT_LABEL.get((sk.get("cats") or ["etc"])[0], "기타")
            when = f'<br><span class="desc">언제: {escape(clip(sk["when"], 120))}</span>' if sk.get("when") else ""
            items += (f'<li><b>{escape(clip(sk["name"], 40))}</b> <span class="tag t-auto">{escape(cat)}</span>'
                      f'<span class="desc">{escape(clip(sk["what"], 100))}</span>{when}</li>')
        title = "주요 스킬 (원문 설명)"
    more = g.get("unique_skills", 0) - len(shown)
    return title, items + (f'<li class="none">외 {fmt_int(more)}개</li>' if more > 0 else "")


def _matrix_html(snapshot: dict) -> str:
    if not has_guides(snapshot):
        return ""
    facts = guide_mod.CLI_FACTS
    head = "".join(f"<th>{escape(f['short'])}</th>" for f in facts)
    rows = ""
    for e in snapshot["top"]:
        g = guide_of(e)
        if not g:
            continue
        levels = {c["id"]: c["level"] for c in g["compat"]["clis"]}
        cells = "".join(f"<td>{_lv(levels[f['id']], short=True) if f['id'] in levels else '–'}</td>" for f in facts)
        rows += (f'<tr><td class="num">{e["rank"]}</td><td><div class="repo">{escape(e["full_name"])}</div>'
                 f'<div class="desc">{escape(g.get("kind", ""))} · 고유 스킬 {fmt_int(g.get("unique_skills", 0))}개</div></td>'
                 f'{cells}<td><b>{escape(g["compat"].get("optimized_label", "–"))}</b></td></tr>')
    legend = "".join(f'{_lv(k)} {escape(v)}<br>' for k, v in guide_mod.LEVEL_HELP.items())
    return (f'<div class="sect"><h2>CLI 호환성 한눈에</h2><table class="matrix"><thead><tr><th class="num">#</th><th>레포</th>{head}'
            f'<th>최적화 대상</th></tr></thead><tbody>{rows}</tbody></table>'
            f'<p class="legend">{legend}최적화 대상은 레포에 들어 있는 패키징·설치 안내·전용 기능 사용을 근거로 한 추정이며, '
            '각 CLI 에서 실제로 실행해 본 결과가 아닙니다.</p></div>')


def _methods_html() -> str:
    rows = "".join(f'<tr><td>{escape(f["label"])}</td><td>{_inline(f["where"])}</td><td>{_inline(f["package"])}</td></tr>'
                   for f in guide_mod.CLI_FACTS)
    updated = escape(guide_mod.load_notes().get("updated") or "날짜 미상")
    return f"""<li><b>활용 가이드</b> — ‘요약·이럴 때 쓴다·알아둘 점’은 각 레포의 README 와 SKILL.md 를 읽고 정리한 것입니다({updated} 기준). 요약이 아직 없는 레포는 LLM 이 README 와 SKILL.md 설명을 요약한 <span class="tag t-ai">AI 요약</span>(사람이 검토하지 않은 글, LLM 설정이 있을 때만) 또는 SKILL.md description 과 키워드 분류로 만든 ‘자동 분석’으로 표시합니다.</li>
<li><b>CLI 수준·최적화 대상</b> — 레포 안의 매니페스트, 스킬이 놓인 폴더, README 설치 안내, 특정 CLI 전용 기능(frontmatter 확장 키·치환 변수·도구 이름) 사용을 근거로 한 추정입니다. 각 CLI 에서 실행해 검증한 값이 아니며, ‘다른 CLI 에서는 안 된다’는 판단은 그 CLI 공식 문서에 해당 기능이 없다는 데서 온 추론입니다. <span class="tag t-auto">일반</span> 표시는 <code>npx skills add</code> 기준의 일반 절차로 레포별 검증은 하지 않았습니다.</li>
</ul>
<h2>CLI 별 스킬 위치 — 공식 문서 기준 ({escape(guide_mod.FACTS_VERIFIED)} 확인)</h2>
<table class="facts"><thead><tr><th>CLI</th><th>스킬을 읽는 위치</th><th>묶음(패키징)</th></tr></thead><tbody>{rows}</tbody></table>
<ul class="note" style="margin-top:6pt"><li>여섯 CLI 모두 표준 SKILL.md(name·description frontmatter)를 그대로 읽습니다. 차이는 읽는 폴더와 플러그인 형식입니다.</li>"""


def _change_html(entry: dict) -> str:
    text = rank_change(entry)
    cls = {"N": "new", "▲": "up", "▼": "down"}.get(text[0], "none")
    path = rank_path(entry)
    detail = f'<div class="path">{escape(path)}</div>' if path else ""
    return f'<span class="{cls}">{escape(text)}</span>{detail}'


def _delta_html(delta) -> str:
    text = fmt_delta(delta)
    cls = "none" if delta in (None, 0) else ("up" if delta > 0 else "down")
    return f'<span class="{cls}">{escape(text)}</span>'


def _card(e: dict) -> str:
    t, c = e["test"], e["test"]["counts"]
    high = sum(f["count"] for f in t["flag_summary"] if f["severity"] == "high")
    medium = sum(f["count"] for f in t["flag_summary"] if f["severity"] == "medium")
    checks = [
        ("규격 통과", ratio(c["spec_pass"], c["skills"])),
        ("권고까지 무결", ratio(c["clean"], c["skills"])),
        ("상대 링크 정상", ratio(c["links_total"] - c["links_broken"], c["links_total"])),
        ("스크립트 문법 정상", ratio(c["scripts_total"] - c["scripts_bad"], c["scripts_total"])),
        ("안전 신호", f"높음 {high} · 보통 {medium}" if high or medium else "없음"),
    ]
    findings = "".join(
        f'<li><span class="tag t-{g["level"]}">{LEVEL_LABEL[g["level"]]}</span>'
        f'{escape(g["kind"])} × {g["count"]}<br><code>{escape(g["example"][:90])}</code></li>'
        for g in t["issue_summary"])
    findings += "".join(
        f'<li><span class="tag t-{f["severity"]}">안전 신호</span>{escape(f["label"])} × {f["count"]}'
        f'<br><code>{escape(f["path"][:84])}:{f["line"]}</code></li>'
        for f in t["flag_summary"])
    if not findings:
        findings = '<li class="none">발견된 문제 없음</li>'
    skills_title, skills = _skills_html(e)
    guide_block = _guide_html(e)
    trunc = " · 상한 초과로 일부만 검사" if t.get("truncated") else ""
    delta = f" ({fmt_delta(e['stars_delta'])})" if e.get("stars_delta") is not None else ""
    return f"""
<div class="card{' long' if guide_block else ''}">
  <h3><span class="rk">{escape(e.get('rank_label') or '#' + str(e['rank']))}</span><span>{escape(e['full_name'])}</span>
      <span class="sc"><span class="grade g{t['grade']}">{t['grade']}</span> {t['score']}점</span></h3>
  <div class="meta">★ {fmt_int(e['stars'])}{escape(delta)} · 포크 {fmt_int(e['forks'])}
    · 라이선스 {escape(e.get('license') or '미표기')} · {escape(e.get('language') or '언어 미표기')}
    · 최근 푸시 {escape((e.get('pushed_at') or '')[:10])} · 검사 커밋 <code>{escape((t.get('commit') or '')[:7])}</code>
    · 스킬 {fmt_int(c['skills'])}개{trunc}</div>
  <p class="cdesc">{escape(clip(e['description'], 260)) or '<span class="none">(설명 없음)</span>'}</p>
  {guide_block}
  <div class="blk"><div class="checks">{''.join(f'<div class="check"><b>{escape(v)}</b><span>{escape(k)}</span></div>' for k, v in checks)}</div>
  <div class="cols">
    <div><h4>주요 발견</h4><ul>{findings}</ul></div>
    <div><h4>{skills_title}</h4><ul>{skills}</ul></div>
  </div></div>
</div>"""


def build_html(snapshot: dict) -> str:
    ov = overview(snapshot)
    grades = " · ".join(f"{g} {ov['grades'][g]}" for g in "ABCD" if ov["grades"].get(g))
    rows = "".join(f"""
<tr><td class="num">{e['rank']}</td><td class="ctr">{_change_html(e)}</td>
<td><div class="repo">{repo_link(e['full_name'])}</div><div class="desc">{escape(clip(e['description'], 120))}</div></td>
<td class="num">{fmt_int(e['stars'])}</td><td class="num">{_delta_html(e.get('stars_delta'))}</td>
<td class="num">{fmt_int(e['test']['counts']['skills'])}</td><td class="num">{e['test']['score']}</td>
<td class="ctr"><span class="grade g{e['test']['grade']}">{e['test']['grade']}</span></td></tr>"""
                   for e in snapshot["top"])
    if snapshot.get("rising"):
        rising_rows = "".join(
            f'<tr><td><div class="repo">{repo_link(r["full_name"])}</div>'
            f'<div class="desc">{escape(clip(r["description"], 120))}</div></td>'
            f'<td class="num">{r.get("rank") or "–"}</td><td class="ctr">{_change_html(r)}</td>'
            f'<td class="num">{fmt_int(r["stars"])}</td><td class="num">{_delta_html(r["delta"])}</td></tr>'
            for r in snapshot["rising"])
        rising = (f'<h2>급상승 — {escape(snapshot["previous_date"])} 대비 스타 증가</h2><table><thead><tr><th>레포</th>'
                  f'<th class="num">순위</th><th class="ctr">순위 변동</th>'
                  f'<th class="num">★ 스타</th><th class="num">증가</th></tr></thead><tbody>{rising_rows}</tbody></table>')
    elif snapshot.get("previous_date"):
        rising = ""
    else:
        rising = ('<h2>급상승</h2><p class="note">오늘이 첫 수집일이라 비교 대상이 없습니다. '
                  '다음 리포트부터 전일 대비 스타 증가 상위 레포가 표시됩니다.</p>')
    if snapshot.get("previous_date"):
        if snapshot.get("climbers"):
            climb_rows = "".join(
                f'<tr><td class="repo">{repo_link(c["full_name"])}</td>'
                f'<td class="ctr"><span class="up">{escape(rank_change(c))}</span></td>'
                f'<td class="ctr">{escape(rank_path(c))}</td>'
                f'<td class="num">{fmt_int(c["stars"])}</td><td class="num">{_delta_html(c["delta"])}</td></tr>'
                for c in snapshot["climbers"])
            climbers = ('<table><thead><tr><th>레포</th><th class="ctr">상승 폭</th><th class="ctr">순위</th>'
                        f'<th class="num">★ 스타</th><th class="num">증가</th></tr></thead><tbody>{climb_rows}</tbody></table>')
        else:
            climbers = f'<p class="note">{escape(snapshot["previous_date"])} 대비 순위가 오른 레포가 없습니다.</p>'
        rising += (f'<h2>순위 상승 — {escape(snapshot["previous_date"])} 대비</h2>{climbers}'
                   f'<p class="note" style="margin-top:5pt">{escape(RANK_NOTE)}</p>')
    excluded = ""
    if snapshot.get("excluded"):
        ex_rows = "".join(f'<tr><td class="repo">{escape(x["full_name"])}</td><td class="num">{fmt_int(x["stars"])}</td>'
                          f'<td>{escape(x["reason"])}</td></tr>' for x in snapshot["excluded"])
        excluded = ('<h2>제외된 후보</h2><p class="note">스타 순위는 높지만 아래 사유로 Top 목록에서 빠졌습니다.</p>'
                    '<table><thead><tr><th>레포</th><th class="num">★ 스타</th><th>사유</th></tr></thead>'
                    f'<tbody>{ex_rows}</tbody></table>')
    warn = ""
    if snapshot.get("search_failures"):
        warn = ('<p class="note down">일부 검색어가 실패해 후보 풀이 평소보다 작을 수 있습니다: '
                + escape("; ".join(snapshot["search_failures"])[:300]) + "</p>")
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>AI Skills 일일 리포트 {escape(snapshot['date'])}</title><style>{CSS}</style></head><body>
<div class="navbar"><a href="index.html">← 날짜별 리포트</a> · <a href="{escape(snapshot['date'])}.pdf">PDF 받기</a></div>
<div class="kicker">SKILLS TRACKER · DAILY</div>
<h1>AI Skills 일일 리포트 — {escape(snapshot['date'])}</h1>
<div class="sub">GitHub 스타 상위 에이전트 스킬 레포 수집 및 자체 정적 테스트 결과 · 생성 {escape(snapshot['generated_at'])} ({escape(snapshot['timezone'])})</div>
<div class="tiles">
  <div class="tile"><b>{fmt_int(snapshot['pool_size'])}</b><span>검색된 후보 레포</span></div>
  <div class="tile"><b>{ov['repos']}</b><span>테스트한 스타 상위 레포</span></div>
  <div class="tile"><b>{fmt_int(ov['skills'])}</b><span>검사한 스킬(SKILL.md)</span></div>
  <div class="tile"><b>{ov['avg']}</b><span>평균 점수 · 등급 {escape(grades)}</span></div>
</div>
{warn}
{_html_highlights(snapshot)}
<h2>스타 상위 Top {ov['repos']}</h2>
<table><thead><tr><th class="num">#</th><th class="ctr">순위 변동</th><th>레포</th><th class="num">★ 스타</th>
<th class="num">전일 대비</th><th class="num">스킬 수</th><th class="num">점수</th><th class="ctr">등급</th></tr></thead>
<tbody>{rows}</tbody></table>
{_html_watch_table(snapshot)}
{_matrix_html(snapshot)}
{rising}
<h2>{'레포별 활용 가이드와 테스트 결과' if has_guides(snapshot) else '레포별 테스트 결과'}</h2>
{''.join(_card(e) for e in snapshot['top'])}
{('<h2>관심 레포 활용 가이드와 테스트 결과</h2>' + ''.join(_card(e) for e in watch_tested(snapshot))) if watch_tested(snapshot) else ''}
{excluded}
<h2>방법과 한계</h2>
<ul class="note">
<li><b>수집</b> — GitHub 검색 API의 읽기(GET) 요청만 사용해 스킬 관련 토픽·이름으로 후보를 모으고 스타 내림차순으로 정렬합니다. 실제로 SKILL.md 를 가진 레포만 순위에 넣습니다.</li>
<li><b>타 레포 비간섭</b> — star·fork·issue·PR·댓글 등 쓰기 동작이 전혀 없고, 레포당 얕은(depth 1) 스파스 클론 1회만 수행하며 요청 사이에 간격을 둡니다.</li>
<li><b>테스트</b> — Agent Skills 공개 규격 기준 frontmatter(name·description) 검사, 상대 링크 무결성, 번들 스크립트 문법 검사(Python·셸·JS), 위험 패턴 스캔. 모두 정적 검사이며 내려받은 코드는 실행하지 않습니다.</li>
<li><b>점수</b> — 규격 55 + 품질 권고 15 + 링크 15 + 스크립트 15 = 100점. A 90↑ · B 75↑ · C 60↑ · D 60 미만. 규격 준수와 무결성을 잰 값이며 스킬의 실제 효용을 보증하지 않습니다.</li>
<li><b>안전 신호</b> — 사람이 직접 확인할 위치를 알려주는 표시이며 악성 판정이 아닙니다. 보안 주제 스킬이 나쁜 예시로 해당 문구를 싣는 등 오탐이 섞이므로 점수에는 반영하지 않습니다.</li>
{_methods_html() if has_guides(snapshot) else ''}
</ul>
</body></html>"""


# ------------------------------------------------------------------ 웹 목록 --

def build_index(entries: list[dict]) -> str:
    """날짜별 리포트 목록 페이지. entries: [{date, top1, avg, climbers, moved}] (최신순)."""
    rows = "".join(
        f'<tr><td class="repo"><a class="rl" href="{escape(e["date"])}.html">{escape(e["date"])}</a></td>'
        f'<td>{repo_link(e["top1"]) if e.get("top1") else "–"}</td>'
        f'<td class="num">{e.get("avg") if e.get("avg") is not None else "–"}</td>'
        f'<td>{escape(e.get("headline") or "–")}</td>'
        f'<td class="ctr"><a href="{escape(e["date"])}.html">웹</a> · <a href="{escape(e["date"])}.pdf">PDF</a></td></tr>'
        for e in entries)
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>AI Skills 리포트</title><style>{CSS}</style></head><body>
<div class="kicker">SKILLS TRACKER · DAILY</div>
<h1>AI Skills 일일 리포트</h1>
<div class="sub">GitHub 스타 상위 에이전트 스킬 레포를 매일 수집·정적 테스트한 결과입니다. 날짜를 누르면 리포트가 열립니다.</div>
<table><thead><tr><th>날짜</th><th>1위</th><th class="num">평균 점수</th><th>오늘의 한 줄</th><th class="ctr">보기</th></tr></thead>
<tbody>{rows}</tbody></table>
</body></html>"""


def headline(snapshot: dict) -> str:
    """목록 페이지에 쓸 한 줄 요약."""
    h = snapshot.get("highlights")
    if not h or not h.get("previous_date"):
        return "첫 수집"
    parts = []
    moved = h["top"]["moved"]
    parts.append(f"Top 순위 변동 {len(moved)}건" if moved else "Top 순위 변동 없음")
    if h["top"]["entered"]:
        parts.append("신규 진입 " + ", ".join(n["full_name"] for n in h["top"]["entered"]))
    if h["climbers"]:
        c = h["climbers"][0]
        parts.append(f"상승 1위 {c['full_name']} ▲{c['prev_rank'] - c['rank']}")
    return " · ".join(parts)


# --------------------------------------------------------------------- PDF --

def find_chrome() -> str | None:
    candidates = [os.environ.get("CHROME_BIN"), "google-chrome", "google-chrome-stable",
                  "chromium", "chromium-browser", "/opt/pw-browsers/chromium"]
    for cand in candidates:
        if not cand:
            continue
        path = cand if os.path.isabs(cand) and os.path.exists(cand) else shutil.which(cand)
        if path:
            return path
    return None


def ensure_korean_font() -> None:
    """한글 폰트가 없으면 글자가 깨진 PDF 가 조용히 만들어지므로 미리 막는다."""
    if not shutil.which("fc-list"):
        return
    proc = subprocess.run(["fc-list", ":lang=ko", "family"], capture_output=True, text=True, timeout=30)
    if proc.returncode == 0 and not proc.stdout.strip():
        raise RuntimeError("한글 폰트가 없습니다 (예: apt-get install fonts-noto-cjk)")


def html_to_pdf(html: str, pdf_path: str) -> None:
    ensure_korean_font()
    chrome = find_chrome()
    if not chrome:
        raise RuntimeError("Chrome/Chromium 을 찾을 수 없습니다 (CHROME_BIN 으로 지정 가능)")
    with tempfile.TemporaryDirectory(prefix="skills-report-") as tmp:
        src = os.path.join(tmp, "report.html")
        with open(src, "w", encoding="utf-8") as fh:
            fh.write(html)
        cmd = [chrome, "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
               f"--user-data-dir={os.path.join(tmp, 'profile')}", "--no-pdf-header-footer",
               f"--print-to-pdf={os.path.abspath(pdf_path)}", "file://" + src]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if proc.returncode != 0 or not os.path.exists(pdf_path) or os.path.getsize(pdf_path) < 1000:
        raise RuntimeError("PDF 생성 실패: " + proc.stderr.strip()[-300:])
