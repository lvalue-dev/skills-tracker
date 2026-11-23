"""GitHub 검색 API(읽기 전용 GET)로 스킬 레포 후보를 모은다.

`search/repositories` 조회만 한다. `gh` CLI 가 있으면 그것으로, 없으면(홈서버 등)
HTTPS 로 직접 호출한다. 다른 레포에 쓰는 호출(star, fork, issue, PR, comment)은
이 코드베이스 어디에도 없다.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

FIELDS = (
    "full_name", "html_url", "description", "stargazers_count", "forks_count",
    "language", "topics", "pushed_at", "created_at", "default_branch", "size",
)
API_URL = "https://api.github.com/search/repositories"
TOKENLESS_MIN_GAP = 7.0  # 토큰 없는 검색 한도(분당 10회)를 넘지 않기 위한 최소 간격


class SearchError(RuntimeError):
    pass


def api_token() -> str:
    return (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()


def _search_gh(query: str, per_page: int) -> list[dict]:
    cmd = [
        "gh", "api", "-X", "GET", "search/repositories",
        "-f", f"q={query}", "-f", "sort=stars", "-f", "order=desc",
        "-f", f"per_page={per_page}",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise SearchError((proc.stderr or proc.stdout).strip()[:300])
    return json.loads(proc.stdout).get("items", [])


def _search_http(query: str, per_page: int) -> list[dict]:
    params = {"q": query, "sort": "stars", "order": "desc", "per_page": per_page}
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "python-urllib",
               "X-GitHub-Api-Version": "2022-11-28"}
    if api_token():
        headers["Authorization"] = f"Bearer {api_token()}"
    request = urllib.request.Request(f"{API_URL}?{urllib.parse.urlencode(params)}", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response).get("items", [])
    except urllib.error.HTTPError as exc:
        raise SearchError(f"HTTP {exc.code} {exc.reason}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise SearchError(str(exc)[:300]) from exc


def backends() -> list:
    """사용할 조회 수단 (앞에서부터 시도). gh 가 없거나 실패하면 HTTPS 로 넘어간다."""
    return ([_search_gh] if shutil.which("gh") else []) + [_search_http]


def _search(query: str, per_page: int) -> list[dict]:
    last = ""
    for attempt in range(4):
        for backend in backends():
            try:
                return backend(query, per_page)
            except (SearchError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
                last = f"{getattr(backend, '__name__', 'search')}: {exc}"
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
    gap = cfg["seconds_between_api_calls"]
    if not shutil.which("gh") and not api_token():
        gap = max(gap, TOKENLESS_MIN_GAP)
    for i, query in enumerate(cfg["search_queries"]):
        if i:
            time.sleep(gap)
        full_query = f"{query} {qualifiers}".strip()
        try:
            items = _search(full_query, cfg["per_query_results"])
        except SearchError as exc:
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


def fetch_repos(names: list[str], cfg: dict) -> tuple[list[dict], list[str]]:
    """관심 레포의 메타데이터를 검색 API(`repo:소유자/이름`)로 가져온다. 순위 검색과 같은 읽기 전용 GET.

    돌려주는 순서는 names 순서이고, 찾지 못한 이름은 두 번째 값으로 돌려준다.
    """
    found: dict[str, dict] = {}
    for i in range(0, len(names), 5):  # 검색어 길이 제한(256자) 안에서 몇 개씩 묶는다
        if i:
            time.sleep(cfg["seconds_between_api_calls"])
        query = " ".join(f"repo:{n}" for n in names[i:i + 5])
        try:
            items = _search(query, 10)
        except SearchError as exc:
            print(f"[watch] WARN {exc}", file=sys.stderr)
            continue
        for item in items:
            repo = _slim(item)
            found[repo["full_name"].lower()] = repo
    repos = [found[n.lower()] for n in names if n.lower() in found]
    missing = [n for n in names if n.lower() not in found]
    return repos, missing

