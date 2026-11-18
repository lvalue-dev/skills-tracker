"""일일 실행: 수집 → 읽기 전용 가져오기 → 정적 테스트 → 스냅샷/리포트 저장.

    python -m tracker [--date YYYY-MM-DD] [--skip-pdf] [--render-only]
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from zoneinfo import ZoneInfo

from . import checks, collect, fetch, report

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_previous(data_dir: str, date: str) -> dict | None:
    earlier = sorted(p for p in glob.glob(os.path.join(data_dir, "*.json"))
                     if os.path.basename(p)[:-5] < date)
    if not earlier:
        return None
    with open(earlier[-1], encoding="utf-8") as fh:
        return json.load(fh)


def apply_history(top: list[dict], pool: list[dict], previous: dict | None) -> list[dict]:
    """전일 스냅샷과 비교해 스타 증감·순위 변동·급상승 목록을 채운다."""
    if not previous:
        for entry in top:
            entry.update(stars_delta=None, prev_rank=None, is_new=False)
        return []
    prev_stars = {r["full_name"]: r["stars"] for r in previous.get("pool", [])}
    prev_rank = {e["full_name"]: e["rank"] for e in previous.get("top", [])}
    for entry in top:
        name = entry["full_name"]
        entry["stars_delta"] = entry["stars"] - prev_stars[name] if name in prev_stars else None
        entry["prev_rank"] = prev_rank.get(name)
        entry["is_new"] = name not in prev_rank
    rising = [{"full_name": r["full_name"], "stars": r["stars"],
               "delta": r["stars"] - prev_stars[r["full_name"]], "description": r["description"]}
              for r in pool if r["full_name"] in prev_stars]
    rising = [r for r in rising if r["delta"] > 0]
    rising.sort(key=lambda r: (-r["delta"], r["full_name"]))
    return rising[:10]


def test_candidates(pool: list[dict], cfg: dict) -> tuple[list[dict], list[dict]]:
    """스타 순으로 내려가며 SKILL.md 를 가진 레포 top_n 개를 테스트한다."""
    top, excluded = [], []
    limits = cfg["limits"]
    workdir = tempfile.mkdtemp(prefix="skills-tracker-")
    try:
        for checked, repo in enumerate(pool[: cfg["max_candidates_checked"]]):
            if len(top) >= cfg["top_n"]:
                break
            if checked:
                time.sleep(cfg["seconds_between_clones"])
            name = repo["full_name"]
            dest = os.path.join(workdir, name.replace("/", "__"))
            print(f"[test] {name} (★{repo['stars']:,})", file=sys.stderr)
            try:
                fetched = fetch.fetch_repo(name, dest, limits)
                if not fetched["skill_dirs"]:
                    excluded.append({"full_name": name, "stars": repo["stars"],
                                     "reason": "SKILL.md 없음 (스킬 묶음이 아닌 도구·목록 레포)"})
                    continue
                result = checks.analyze_repo(dest, fetched, limits)
                result["commit"] = subprocess.run(
                    ["git", "rev-parse", "HEAD"], cwd=dest, capture_output=True, text=True,
                    env=fetch.safe_env()).stdout.strip()
            except fetch.FetchError as exc:
                excluded.append({"full_name": name, "stars": repo["stars"],
                                 "reason": f"가져오기 실패: {exc}"})
                continue
            finally:
                shutil.rmtree(dest, ignore_errors=True)
            result["issue_summary"] = report.summarize_issues(result["issues"])
            result["flag_summary"] = report.summarize_flags(result["flags"])
            result["issues"] = result["issues"][:40]
            result["flags"] = result["flags"][:40]
            clean_first = sorted(result["skills"], key=lambda s: (s["errors"] > 0, not s["description"]))
            result["skills"] = clean_first[:40]
            top.append({**repo, "rank": len(top) + 1, "test": result})
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return top, excluded


def write_index(reports_dir: str) -> None:
    dates = sorted((os.path.basename(p)[:-4] for p in glob.glob(os.path.join(reports_dir, "*.pdf"))),
                   reverse=True)
    lines = ["# 일자별 리포트", "", "| 날짜 | PDF | Markdown |", "|---|---|---|"]
    lines += [f"| {d} | [{d}.pdf]({d}.pdf) | [{d}.md]({d}.md) |" for d in dates]
    with open(os.path.join(reports_dir, "README.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def write_reports(snapshot: dict, reports_dir: str, skip_pdf: bool) -> None:
    date = snapshot["date"]
    with open(os.path.join(reports_dir, f"{date}.md"), "w", encoding="utf-8") as fh:
        fh.write(report.build_markdown(snapshot))
    if not skip_pdf:
        report.html_to_pdf(report.build_html(snapshot), os.path.join(reports_dir, f"{date}.pdf"))
    write_index(reports_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="리포트 날짜 (기본: 설정 시간대의 오늘)")
    parser.add_argument("--skip-pdf", action="store_true")
    parser.add_argument("--render-only", action="store_true",
                        help="네트워크 접근 없이 저장된 data/<날짜>.json 으로 리포트만 다시 만든다")
    args = parser.parse_args()

    with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as fh:
        cfg = json.load(fh)
    now = dt.datetime.now(ZoneInfo(cfg["timezone"]))
    date = args.date or now.strftime("%Y-%m-%d")
    data_dir, reports_dir = os.path.join(ROOT, "data"), os.path.join(ROOT, "reports")
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(reports_dir, exist_ok=True)

    if args.render_only:
        with open(os.path.join(data_dir, f"{date}.json"), encoding="utf-8") as fh:
            write_reports(json.load(fh), reports_dir, args.skip_pdf)
        return 0

    pool, failures = collect.collect_pool(cfg)
    top, excluded = test_candidates(pool, cfg)
    if not top:
        print("테스트 가능한 스킬 레포를 찾지 못했습니다.", file=sys.stderr)
        return 1
    previous = load_previous(data_dir, date)
    rising = apply_history(top, pool, previous)

    snapshot = {
        "date": date, "generated_at": now.strftime("%Y-%m-%d %H:%M"), "timezone": cfg["timezone"],
        "previous_date": previous["date"] if previous else None,
        "pool_size": len(pool), "search_failures": failures,
        "top": top, "rising": rising, "excluded": excluded,
        "pool": [{"full_name": r["full_name"], "stars": r["stars"]} for r in pool],
    }
    with open(os.path.join(data_dir, f"{date}.json"), "w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, ensure_ascii=False, indent=1)
    write_reports(snapshot, reports_dir, args.skip_pdf)
    print(f"[done] {date}: top {len(top)}, excluded {len(excluded)}, pool {len(pool)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
