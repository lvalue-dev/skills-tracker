"""공개 레포를 읽기 전용으로 가져온다 (depth 1 · blob 없는 스파스 클론).

- 원격에 쓰는 git 명령(push 등)은 없다.
- 트리 목록만 먼저 받고, SKILL.md 가 있는 디렉터리의 파일만 내려받아
  상대 서버 전송량을 최소화한다.
- 서브모듈·LFS·심볼릭 링크는 받지 않으며, 토큰은 git 프로세스에 넘기지 않는다.
"""
from __future__ import annotations

import os
import posixpath
import subprocess

SECRET_ENV = ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN")
ROOT_SKILL_SUBDIRS = ("scripts", "references", "reference", "assets", "templates",
                      "resources", "examples", "docs")


class FetchError(RuntimeError):
    pass


def safe_env() -> dict:
    """서드파티 콘텐츠를 다루는 하위 프로세스용 환경 (토큰 제거)."""
    env = {k: v for k, v in os.environ.items() if k not in SECRET_ENV}
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_LFS_SKIP_SMUDGE"] = "1"
    return env


def _git(args: list[str], cwd: str, timeout: int) -> subprocess.CompletedProcess:
    base = ["git", "-c", "protocol.file.allow=never", "-c", "core.symlinks=false",
            "-c", "core.hooksPath=/dev/null", "-c", "advice.detachedHead=false"]
    try:
        proc = subprocess.run(base + args, cwd=cwd, env=safe_env(),
                              capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise FetchError(f"git {args[0]} timed out after {timeout}s") from exc
    if proc.returncode != 0:
        msg = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or ["?"]
        raise FetchError(f"git {args[0]} failed: {msg[0][:200]}")
    return proc


def _escape_pattern(path: str) -> str:
    out = []
    for ch in path:
        if ch in "\\*?[]!#":
            out.append("\\")
        out.append(ch)
    return "".join(out)


def skill_dirs_from_tree(paths: list[str]) -> list[str]:
    """SKILL.md 를 가진 디렉터리 목록 (레포 루트는 빈 문자열)."""
    dirs = {posixpath.dirname(p) for p in paths if posixpath.basename(p) == "SKILL.md"}
    return sorted(dirs)


def root_readme(tree: dict[str, str]) -> str | None:
    """루트의 README (정규 파일만 — 심볼릭 링크는 제외). 없으면 None."""
    found = [p for p, mode in tree.items()
             if "/" not in p and mode in ("100644", "100755")
             and p.lower() in ("readme.md", "readme.markdown", "readme", "readme.txt", "readme.rst")]
    return min(found, key=lambda p: (p.lower() != "readme.md", p)) if found else None


def fetch_repo(full_name: str, dest: str, limits: dict) -> dict:
    """트리 목록을 받고 스킬 디렉터리(와 루트 README)만 체크아웃한다.

    반환: {"tree": {path: mode}, "skill_dirs": [...], "truncated": bool, "checked_out": bool,
           "readme": 루트 README 경로 | None}
    """
    url = f"https://github.com/{full_name}.git"
    parent = os.path.dirname(dest)
    _git(["clone", "--depth", "1", "--filter=blob:none", "--no-checkout",
          "--single-branch", "--no-tags", "--no-recurse-submodules", url, dest],
         cwd=parent, timeout=limits["clone_timeout_seconds"])
    listing = _git(["ls-tree", "-r", "-z", "HEAD"], cwd=dest, timeout=120).stdout
    tree: dict[str, str] = {}
    for record in listing.split(b"\0"):
        if not record:
            continue
        meta, _, raw_path = record.partition(b"\t")
        tree[raw_path.decode("utf-8", "replace")] = meta.split()[0].decode()

    skill_dirs = skill_dirs_from_tree(list(tree))
    truncated = len(skill_dirs) > limits["max_skills_per_repo"]
    skill_dirs = skill_dirs[: limits["max_skills_per_repo"]]
    result = {"tree": tree, "skill_dirs": skill_dirs, "truncated": truncated,
              "checked_out": False, "readme": root_readme(tree)}
    if not skill_dirs:
        return result

    patterns: list[str] = []
    for d in skill_dirs:
        if d == "":
            # 루트 스킬: 최상위 파일과 관례적 하위 폴더만 받는다 (레포 전체는 받지 않음).
            patterns += ["/*", "!/*/"]
            patterns += [f"/{sub}/" for sub in ROOT_SKILL_SUBDIRS]
        else:
            patterns.append("/" + _escape_pattern(d) + "/")
    if result["readme"]:
        # 활용 가이드(설치 방법·지원 CLI)용. 읽기만 한다.
        patterns.append("/" + _escape_pattern(result["readme"]))
    _git(["sparse-checkout", "init", "--no-cone"], cwd=dest, timeout=60)
    with open(os.path.join(dest, ".git", "info", "sparse-checkout"), "w",
              encoding="utf-8") as fh:
        fh.write("\n".join(patterns) + "\n")
    _git(["checkout", "--quiet", "HEAD"], cwd=dest,
         timeout=limits["checkout_timeout_seconds"])
    result["checked_out"] = True
    return result
