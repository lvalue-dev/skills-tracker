"""GitHub 검색 API(읽기 전용 GET)로 스킬 레포 후보를 모은다.

`gh api -X GET search/repositories` 만 호출한다. 다른 레포에 쓰는 호출
(star, fork, issue, PR, comment)은 이 코드베이스 어디에도 없다.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time

FIELDS = (
    "full_name", "html_url", "description", "stargazers_count", "forks_count",
    "language", "topics", "pushed_at", "created_at", "default_branch", "size",
)


class SearchError(RuntimeError):
    pass


def _search(query: str, per_page: int) -> list[dict]:
    cmd = [
        "gh", "api", "-X", "GET", "search/repositories",
        "-f", f"q={query}", "-f", "sort=stars", "-f", "order=desc",
        "-f", f"per_page={per_page}",
    ]
    last = ""
    for attempt in range(4):
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if proc.returncode == 0:
            return json.loads(proc.stdout).get("items", [])
        last = (proc.stderr or proc.stdout).strip()[:300]
        # 레이트 리밋이면 물러났다가 재시도한다 (상대 서버에 부담을 주지 않기 위함).
        time.sleep(20 * (attempt + 1))
    raise SearchError(f"search failed for {query!r}: {last}")


def _slim(item: dict) -> dict:
    repo = {k: item.get(k) for k in FIELDS}
    repo["stars"] = repo.pop("stargazers_count") or 0
    repo["forks"] = repo.pop("forks_count") or 0
    repo["license"] = (item.get("license") or {}).get("spdx_id")
    repo["description"] = (repo["description"] or "").strip()
    repo["topics"] = repo["topics"] or []
    return repo


def collect_pool(cfg: dict) -> tuple[list[dict], list[str]]:
    """모든 검색어의 결과를 합쳐 스타 내림차순 후보 풀을 돌려준다."""
    pool: dict[str, dict] = {}
    failures: list[str] = []
    qualifiers = cfg.get("search_qualifiers", "").strip()
    for i, query in enumerate(cfg["search_queries"]):
        if i:
            time.sleep(cfg["seconds_between_api_calls"])
        full_query = f"{query} {qualifiers}".strip()
        try:
            items = _search(full_query, cfg["per_query_results"])
        except (SearchError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
            failures.append(f"{query}: {exc}")
            print(f"[collect] WARN {exc}", file=sys.stderr)
            continue
        print(f"[collect] {query!r}: {len(items)} repos", file=sys.stderr)
        for item in items:
            repo = _slim(item)
            entry = pool.setdefault(repo["full_name"], repo)
            entry.setdefault("matched_queries", []).append(query)
    if not pool:
        raise SearchError("모든 검색이 실패했습니다: " + "; ".join(failures))
    ranked = sorted(pool.values(), key=lambda r: (-r["stars"], r["full_name"]))
    return ranked, failures
