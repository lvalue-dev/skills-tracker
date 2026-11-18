"""스냅샷(JSON) → Markdown / HTML / PDF 리포트."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from collections import Counter
from html import escape

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
    if entry.get("is_new"):
        return "NEW"
    prev = entry.get("prev_rank")
    if prev is None:
        return "–"
    diff = prev - entry["rank"]
    return "–" if diff == 0 else (f"▲{diff}" if diff > 0 else f"▼{-diff}")


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


# ---------------------------------------------------------------- Markdown --

def _md(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ").replace("<", "&lt;")


def build_markdown(snapshot: dict) -> str:
    ov = overview(snapshot)
    out = [f"# AI Skills 일일 리포트 — {snapshot['date']}", "",
           f"- 생성: {snapshot['generated_at']} ({snapshot['timezone']})",
           f"- 후보 풀 {fmt_int(snapshot['pool_size'])}개 레포 중 SKILL.md 를 실제로 가진 "
           f"스타 상위 {ov['repos']}개를 테스트 (스킬 {fmt_int(ov['skills'])}개, 평균 {ov['avg']}점)",
           f"- PDF: [{snapshot['date']}.pdf]({snapshot['date']}.pdf)", "",
           "## 스타 상위 Top " + str(ov["repos"]), "",
           "| # | 변동 | 레포 | ★ 스타 | 전일 대비 | 스킬 수 | 점수 | 등급 |",
           "|--:|:--:|---|--:|--:|--:|--:|:--:|"]
    for e in snapshot["top"]:
        t = e["test"]
        out.append(f"| {e['rank']} | {rank_change(e)} | [{e['full_name']}]({e['html_url']}) | "
                   f"{fmt_int(e['stars'])} | {fmt_delta(e.get('stars_delta'))} | "
                   f"{fmt_int(t['counts']['skills'])} | {t['score']} | {t['grade']} |")
    out.append("")
    if snapshot.get("rising"):
        out += ["## 급상승 (전일 대비 스타 증가)", "",
                "| 레포 | ★ 스타 | 증가 | 설명 |", "|---|--:|--:|---|"]
        for r in snapshot["rising"]:
            out.append(f"| [{r['full_name']}](https://github.com/{r['full_name']}) | "
                       f"{fmt_int(r['stars'])} | {fmt_delta(r['delta'])} | {_md(clip(r['description'], 90))} |")
        out.append("")
    out += ["## 레포별 테스트 결과", ""]
    for e in snapshot["top"]:
        t, c = e["test"], e["test"]["counts"]
        out += [f"### {e['rank']}. {e['full_name']} — {t['grade']} ({t['score']}점)", "",
                f"> {_md(e['description']) or '(설명 없음)'}", "",
                f"- ★ {fmt_int(e['stars'])} · 포크 {fmt_int(e['forks'])} · 라이선스 {e.get('license') or '미표기'}"
                f" · 최근 푸시 {(e.get('pushed_at') or '')[:10]} · 검사 커밋 `{(t.get('commit') or '')[:7]}`",
                f"- 규격 통과 {ratio(c['spec_pass'], c['skills'])} · 권고까지 무결 {ratio(c['clean'], c['skills'])}",
                f"- 상대 링크 정상 {ratio(c['links_total'] - c['links_broken'], c['links_total'])}"
                f" · 스크립트 문법 정상 {ratio(c['scripts_total'] - c['scripts_bad'], c['scripts_total'])}"]
        for g in t["issue_summary"]:
            out.append(f"- [{LEVEL_LABEL[g['level']]}] {g['kind']} × {g['count']} (예: `{g['example']}`)")
        for f in t["flag_summary"]:
            out.append(f"- [안전 신호·{f['severity']}] {f['label']} × {f['count']} (예: `{f['path']}:{f['line']}`)")
        out.append("")
    if snapshot.get("excluded"):
        out += ["## 제외된 후보", "", "| 레포 | ★ 스타 | 사유 |", "|---|--:|---|"]
        for x in snapshot["excluded"]:
            out.append(f"| {x['full_name']} | {fmt_int(x['stars'])} | {_md(x['reason'])} |")
        out.append("")
    out += ["## 방법과 한계", "",
            "- 수집: GitHub 검색 API(GET)만 사용, 스타 내림차순. 다른 레포에 대한 쓰기 동작(star·fork·issue·PR·댓글)은 없음.",
            "- 테스트: 얕은 스파스 클론 후 정적 검사만 수행. 내려받은 코드는 실행하지 않음.",
            "- 점수(규격 55 + 품질 권고 15 + 링크 15 + 스크립트 15)는 규격 준수·무결성을 잰 것으로, 스킬의 실제 효용을 보증하지 않음.",
            "- 안전 신호는 사람이 확인할 위치를 알려주는 표시이며 악성 판정이 아님. 오탐이 섞이므로 점수에 반영하지 않음.", ""]
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
"""


def _change_html(entry: dict) -> str:
    text = rank_change(entry)
    cls = {"N": "new", "▲": "up", "▼": "down"}.get(text[0], "none")
    return f'<span class="{cls}">{escape(text)}</span>'


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
    skills = "".join(
        f'<li><b>{escape(clip(s["name"], 48))}</b> <span class="desc">{escape(clip(s["description"], 88))}'
        f'</span></li>' for s in t["skills"][:4])
    more = c["skills"] - min(len(t["skills"]), 4)
    if more > 0:
        skills += f'<li class="none">외 {fmt_int(more)}개</li>'
    trunc = " · 상한 초과로 일부만 검사" if t.get("truncated") else ""
    delta = f" ({fmt_delta(e['stars_delta'])})" if e.get("stars_delta") is not None else ""
    return f"""
<div class="card">
  <h3><span class="rk">#{e['rank']}</span><span>{escape(e['full_name'])}</span>
      <span class="sc"><span class="grade g{t['grade']}">{t['grade']}</span> {t['score']}점</span></h3>
  <div class="meta">★ {fmt_int(e['stars'])}{escape(delta)} · 포크 {fmt_int(e['forks'])}
    · 라이선스 {escape(e.get('license') or '미표기')} · {escape(e.get('language') or '언어 미표기')}
    · 최근 푸시 {escape((e.get('pushed_at') or '')[:10])} · 검사 커밋 <code>{escape((t.get('commit') or '')[:7])}</code>
    · 스킬 {fmt_int(c['skills'])}개{trunc}</div>
  <p class="cdesc">{escape(clip(e['description'], 260)) or '<span class="none">(설명 없음)</span>'}</p>
  <div class="checks">{''.join(f'<div class="check"><b>{escape(v)}</b><span>{escape(k)}</span></div>' for k, v in checks)}</div>
  <div class="cols">
    <div><h4>주요 발견</h4><ul>{findings}</ul></div>
    <div><h4>수록 스킬 예시</h4><ul>{skills}</ul></div>
  </div>
</div>"""


def build_html(snapshot: dict) -> str:
    ov = overview(snapshot)
    grades = " · ".join(f"{g} {ov['grades'][g]}" for g in "ABCD" if ov["grades"].get(g))
    rows = "".join(f"""
<tr><td class="num">{e['rank']}</td><td class="ctr">{_change_html(e)}</td>
<td><div class="repo">{escape(e['full_name'])}</div><div class="desc">{escape(clip(e['description'], 120))}</div></td>
<td class="num">{fmt_int(e['stars'])}</td><td class="num">{_delta_html(e.get('stars_delta'))}</td>
<td class="num">{fmt_int(e['test']['counts']['skills'])}</td><td class="num">{e['test']['score']}</td>
<td class="ctr"><span class="grade g{e['test']['grade']}">{e['test']['grade']}</span></td></tr>"""
                   for e in snapshot["top"])
    if snapshot.get("rising"):
        rising_rows = "".join(
            f'<tr><td><div class="repo">{escape(r["full_name"])}</div>'
            f'<div class="desc">{escape(clip(r["description"], 120))}</div></td>'
            f'<td class="num">{fmt_int(r["stars"])}</td><td class="num">{_delta_html(r["delta"])}</td></tr>'
            for r in snapshot["rising"])
        rising = (f'<h2>급상승 — {escape(snapshot["previous_date"])} 대비 스타 증가</h2><table><thead><tr><th>레포</th>'
                  f'<th class="num">★ 스타</th><th class="num">증가</th></tr></thead><tbody>{rising_rows}</tbody></table>')
    elif snapshot.get("previous_date"):
        rising = ""
    else:
        rising = ('<h2>급상승</h2><p class="note">오늘이 첫 수집일이라 비교 대상이 없습니다. '
                  '다음 리포트부터 전일 대비 스타 증가 상위 레포가 표시됩니다.</p>')
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
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>AI Skills 일일 리포트 {escape(snapshot['date'])}</title><style>{CSS}</style></head><body>
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
<h2>스타 상위 Top {ov['repos']}</h2>
<table><thead><tr><th class="num">#</th><th class="ctr">변동</th><th>레포</th><th class="num">★ 스타</th>
<th class="num">전일 대비</th><th class="num">스킬 수</th><th class="num">점수</th><th class="ctr">등급</th></tr></thead>
<tbody>{rows}</tbody></table>
{rising}
<h2>레포별 테스트 결과</h2>
{''.join(_card(e) for e in snapshot['top'])}
{excluded}
<h2>방법과 한계</h2>
<ul class="note">
<li><b>수집</b> — GitHub 검색 API의 읽기(GET) 요청만 사용해 스킬 관련 토픽·이름으로 후보를 모으고 스타 내림차순으로 정렬합니다. 실제로 SKILL.md 를 가진 레포만 순위에 넣습니다.</li>
<li><b>타 레포 비간섭</b> — star·fork·issue·PR·댓글 등 쓰기 동작이 전혀 없고, 레포당 얕은(depth 1) 스파스 클론 1회만 수행하며 요청 사이에 간격을 둡니다.</li>
<li><b>테스트</b> — Agent Skills 공개 규격 기준 frontmatter(name·description) 검사, 상대 링크 무결성, 번들 스크립트 문법 검사(Python·셸·JS), 위험 패턴 스캔. 모두 정적 검사이며 내려받은 코드는 실행하지 않습니다.</li>
<li><b>점수</b> — 규격 55 + 품질 권고 15 + 링크 15 + 스크립트 15 = 100점. A 90↑ · B 75↑ · C 60↑ · D 60 미만. 규격 준수와 무결성을 잰 값이며 스킬의 실제 효용을 보증하지 않습니다.</li>
<li><b>안전 신호</b> — 사람이 직접 확인할 위치를 알려주는 표시이며 악성 판정이 아닙니다. 보안 주제 스킬이 나쁜 예시로 해당 문구를 싣는 등 오탐이 섞이므로 점수에는 반영하지 않습니다.</li>
</ul>
</body></html>"""


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


def html_to_pdf(html: str, pdf_path: str) -> None:
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
