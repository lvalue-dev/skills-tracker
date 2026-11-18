"""스킬 정적 테스트. 내려받은 코드는 파싱만 하고 절대 실행하지 않는다.

기준: Agent Skills 공개 규격(agentskills.io) — SKILL.md frontmatter 의
name / description 필수, name 형식, description 길이 등.
"""
from __future__ import annotations

import ast
import os
import posixpath
import re
import subprocess
import unicodedata
import urllib.parse
import warnings

import yaml

from .fetch import safe_env

SCRIPT_EXTS = {".py": "python", ".sh": "shell", ".bash": "shell",
               ".js": "node", ".mjs": "node", ".cjs": "node"}
TEXT_EXTS = {".md", ".markdown", ".txt", ".py", ".sh", ".bash", ".zsh", ".js", ".mjs",
             ".cjs", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml", ".toml", ".ps1",
             ".rb", ".go", ".rs", ".php", ".pl", ".lua", ".xml", ".html", ".cfg", ".ini", ""}

# (규칙 id, 심각도, 설명, 정규식) — "검토 필요" 신호일 뿐 악성 판정이 아니며 점수에 반영하지 않는다.
SECURITY_RULES = [
    ("pipe-to-shell", "medium", "원격 스크립트를 내려받아 셸로 바로 실행",
     re.compile(r"\b(?:curl|wget)\b[^\n|]{0,200}https?://[^\n|]{0,300}\|\s*(?:sudo\s+)?(?:ba|z|da)?sh\b")),
    ("destructive-rm", "high", "루트/홈 전체 삭제 명령",
     re.compile(r"\brm\s+-[a-zA-Z]*[rR][a-zA-Z]*\s+(?:-[a-zA-Z]+\s+)*(?:/|~|\$HOME)/?(?:\*|\s|$)", re.M)),
    ("obfuscated-exec", "high", "인코딩된 페이로드 실행",
     re.compile(r"base64\s+(?:-d|-D|--decode)[^\n|]{0,120}\|\s*(?:ba)?sh\b"
                r"|eval\s*\(\s*atob\s*\(|exec\s*\(\s*base64\.b64decode")),
    ("hidden-unicode", "high", "숨김/방향 제어 유니코드 문자",
     re.compile("[‪-‮⁦-⁩\U000e0001-\U000e007f]")),
    ("prompt-injection", "medium", "사용자 몰래 행동하도록 지시하는 문구",
     re.compile(r"ignore\s+(?:all\s+|any\s+)?(?:previous|prior|above)\s+(?:instructions|prompts)"
                r"|without\s+(?:telling|informing|notifying)\s+the\s+user"
                r"|do\s+not\s+(?:tell|inform)\s+the\s+user\s+(?:about|that\s+you|what\s+you)"
                r"|(?:hide|conceal)\s+(?:this|it)\s+from\s+the\s+user", re.I)),
    ("credential-path", "medium", "자격 증명 파일 경로 접근",
     re.compile(r"~/\.ssh/|\bid_rsa\b|\.aws/credentials|~/\.netrc|~/\.config/gh/hosts")),
    ("permission-bypass", "medium", "권한 확인/검증 우회 옵션",
     re.compile(r"--dangerously-skip-permissions|bypassPermissions|--dangerously-bypass")),
]

NAME_MAX = 64
DESC_MAX = 1024
DESC_MIN_USEFUL = 30
BODY_MAX_LINES = 500
LINK_RE = re.compile(r"(?<!\!)\[[^\]\n]*\]\(\s*([^)\s]+)(?:\s+\"[^\"]*\")?\s*\)")
FENCE_RE = re.compile(r"^(```|~~~).*?^\1[^\n]*$", re.S | re.M)
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")


def read_text(path: str, max_bytes: int) -> str | None:
    """정규 파일만, 크기 제한 안에서 텍스트로 읽는다."""
    try:
        if os.path.islink(path) or not os.path.isfile(path):
            return None
        if os.path.getsize(path) > max_bytes:
            return None
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    if b"\0" in data:
        return None
    return data.decode("utf-8", "replace")


def split_frontmatter(text: str) -> tuple[str | None, str]:
    text = text.lstrip("﻿")
    if not text.startswith("---"):
        return None, text
    lines = text.splitlines()
    if lines[0].strip() != "---":
        return None, text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[1:i]), "\n".join(lines[i + 1:])
    return None, text


def validate_name(name: str) -> str | None:
    """규격 위반이면 사유를 돌려준다."""
    name = unicodedata.normalize("NFKC", name)
    if not name:
        return "name 이 비어 있음"
    if len(name) > NAME_MAX:
        return f"name 이 {NAME_MAX}자 초과 ({len(name)}자)"
    if name != name.lower():
        return "name 에 대문자 포함"
    if name.startswith("-") or name.endswith("-") or "--" in name:
        return "name 의 하이픈 위치가 규격 위반"
    if not all(ch.isalnum() or ch == "-" for ch in name):
        return "name 에 허용되지 않는 문자 포함 (소문자·숫자·하이픈만 허용)"
    return None


def check_skill_md(text: str, dir_name: str | None) -> dict:
    """SKILL.md 한 개의 규격 검사. errors=로드 불가/규격 위반, warnings=품질 권고."""
    errors: list[str] = []
    warns: list[str] = []
    name = description = None
    front, body = split_frontmatter(text)
    meta = None
    if front is None:
        errors.append("frontmatter 없음 (--- 블록으로 시작해야 함)")
    else:
        try:
            meta = yaml.safe_load(front)
        except yaml.YAMLError as exc:
            reason = str(getattr(exc, "problem", "") or exc).splitlines()[0][:80]
            errors.append(f"frontmatter YAML 파싱 실패: {reason}")
        else:
            if not isinstance(meta, dict):
                errors.append("frontmatter 가 key: value 매핑이 아님")
                meta = None
    if meta is not None:
        raw_name = meta.get("name")
        if raw_name is None:
            errors.append("name 필드 없음")
        elif not isinstance(raw_name, str):
            errors.append("name 이 문자열이 아님")
        else:
            name = raw_name.strip()
            problem = validate_name(name)
            if problem:
                errors.append(problem)
            elif dir_name is not None and unicodedata.normalize("NFKC", dir_name) != \
                    unicodedata.normalize("NFKC", name):
                warns.append(f"name({name}) 이 폴더명({dir_name})과 다름")
        raw_desc = meta.get("description")
        if raw_desc is None:
            errors.append("description 필드 없음")
        elif not isinstance(raw_desc, str) or not raw_desc.strip():
            errors.append("description 이 비어 있거나 문자열이 아님")
        else:
            description = " ".join(raw_desc.split())
            if len(description) > DESC_MAX:
                errors.append(f"description 이 {DESC_MAX}자 초과 ({len(description)}자)")
            elif len(description) < DESC_MIN_USEFUL:
                warns.append(f"description 이 너무 짧음 ({len(description)}자) — 호출 조건 판단이 어려움")
        compat = meta.get("compatibility")
        if isinstance(compat, str) and len(compat) > 500:
            warns.append("compatibility 가 500자 초과")
    body_lines = body.strip().splitlines()
    if not body_lines:
        warns.append("본문(지침)이 비어 있음")
    elif len(body_lines) > BODY_MAX_LINES:
        warns.append(f"본문이 {len(body_lines)}줄 — {BODY_MAX_LINES}줄 이하 권장 (참조 파일로 분리)")
    return {"name": name, "description": description, "errors": errors,
            "warnings": warns, "body": body}


def extract_relative_links(markdown: str) -> list[str]:
    """코드 블록 밖의 상대 경로 링크만 뽑는다."""
    stripped = INLINE_CODE_RE.sub("", FENCE_RE.sub("", markdown))
    links = []
    for target in LINK_RE.findall(stripped):
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target) or target.startswith(("#", "/", "//")):
            continue
        if any(ch in target for ch in "{}$<>*|") or re.fullmatch(r"[A-Z0-9_]+", target):
            continue  # 템플릿/플레이스홀더
        path = urllib.parse.unquote(target.split("#", 1)[0].split("?", 1)[0])
        if path:
            links.append(path)
    return links


def resolve_link(skill_dir: str, link: str) -> str | None:
    path = posixpath.normpath(posixpath.join(skill_dir, link))
    if path.startswith("../") or path == "..":
        return None
    return "" if path == "." else path


def check_links(skill_dir: str, body: str, tree: dict, tree_dirs: set) -> tuple[int, list[str]]:
    total, broken = 0, []
    for link in extract_relative_links(body):
        total += 1
        target = resolve_link(skill_dir, link)
        if target is None or (target not in tree and target.rstrip("/") not in tree_dirs
                              and target != ""):
            broken.append(link)
    return total, broken


def check_script(path: str, kind: str, max_bytes: int) -> str | None:
    """문법만 확인한다 (실행 없음). 문제 사유 또는 None."""
    if kind == "python":
        source = read_text(path, max_bytes)
        if source is None:
            return None
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                ast.parse(source)
        except SyntaxError as exc:
            return f"Python 문법 오류 (line {exc.lineno}): {str(exc.msg)[:60]}"
        except (ValueError, RecursionError, MemoryError):
            return "Python 파싱 불가"
        return None
    if kind == "shell":
        source = read_text(path, max_bytes)
        if source is None:
            return None
        first = source.split("\n", 1)[0]
        if first.startswith("#!") and not re.search(r"\b(?:ba)?sh\b", first):
            return None  # zsh/fish 등은 bash 로 판정하지 않는다
        cmd = ["bash", "-n", path]
    else:
        if read_text(path, max_bytes) is None:
            return None
        cmd = ["node", "--check", path]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15,
                              env=safe_env(), cwd=os.path.dirname(path) or ".")
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        lines = [ln for ln in proc.stderr.strip().splitlines() if ln.strip()]
        detail = next((ln for ln in lines if "Error" in ln or "error" in ln), lines[-1] if lines else "")
        return f"{'셸' if kind == 'shell' else 'JS'} 문법 오류: {detail.strip()[:80]}"
    return None


def scan_security(text: str) -> list[tuple[str, str, str, int]]:
    hits = []
    for rule_id, severity, label, regex in SECURITY_RULES:
        match = regex.search(text)
        if match:
            line = text.count("\n", 0, match.start()) + 1
            hits.append((rule_id, severity, label, line))
    return hits


def _owning_skill(path: str, skill_dir_set: set) -> str | None:
    """파일이 속한 가장 가까운 스킬 디렉터리."""
    d = posixpath.dirname(path)
    while True:
        if d in skill_dir_set:
            return d
        if not d:
            return None
        d = posixpath.dirname(d)


def analyze_repo(root: str, fetched: dict, limits: dict) -> dict:
    """체크아웃된 스킬 디렉터리 전체를 검사해 레포 단위 요약을 만든다."""
    tree = fetched["tree"]
    skill_dirs = fetched["skill_dirs"]
    skill_dir_set = set(skill_dirs)
    tree_dirs = set()
    for path in tree:
        d = posixpath.dirname(path)
        while d and d not in tree_dirs:
            tree_dirs.add(d)
            d = posixpath.dirname(d)
    max_bytes = limits["max_file_bytes"]

    skills, issues = [], []
    counts = {"skills": len(skill_dirs), "spec_pass": 0, "clean": 0,
              "links_total": 0, "links_broken": 0, "scripts_total": 0, "scripts_bad": 0}
    for d in skill_dirs:
        rel = posixpath.join(d, "SKILL.md") if d else "SKILL.md"
        text = read_text(os.path.join(root, rel), max_bytes * 2)
        if text is None:
            res = {"name": None, "description": None, "body": "",
                   "errors": ["SKILL.md 를 읽을 수 없음 (심볼릭 링크·바이너리·과대 파일)"],
                   "warnings": []}
        else:
            res = check_skill_md(text, posixpath.basename(d) if d else None)
        total, broken = check_links(d, res["body"], tree, tree_dirs)
        counts["links_total"] += total
        counts["links_broken"] += len(broken)
        if broken:
            res["warnings"].append(f"깨진 상대 링크 {len(broken)}개 (예: {broken[0][:60]})")
        if not res["errors"]:
            counts["spec_pass"] += 1
            if not res["warnings"]:
                counts["clean"] += 1
        for msg in res["errors"]:
            issues.append({"level": "error", "path": rel, "message": msg})
        for msg in res["warnings"]:
            issues.append({"level": "warning", "path": rel, "message": msg})
        skills.append({"path": rel, "name": res["name"] or (posixpath.basename(d) or "(root)"),
                       "description": (res["description"] or "")[:300],
                       "errors": len(res["errors"]), "warnings": len(res["warnings"])})

    flags = []
    for path in sorted(tree):
        owner = _owning_skill(path, skill_dir_set)
        if owner is None or tree[path] == "120000":
            continue
        ext = posixpath.splitext(path)[1].lower()
        local = os.path.join(root, path)
        if ext in SCRIPT_EXTS and counts["scripts_total"] < limits["max_scripts_per_repo"] \
                and os.path.isfile(local):
            counts["scripts_total"] += 1
            problem = check_script(local, SCRIPT_EXTS[ext], max_bytes)
            if problem:
                counts["scripts_bad"] += 1
                issues.append({"level": "script", "path": path, "message": problem})
        if ext in TEXT_EXTS:
            text = read_text(local, max_bytes)
            if text:
                for rule_id, severity, label, line in scan_security(text):
                    flags.append({"rule": rule_id, "severity": severity, "label": label,
                                  "path": path, "line": line})

    return {"counts": counts, "skills": skills, "issues": issues, "flags": flags,
            "truncated": fetched["truncated"], **score(counts)}


def score(counts: dict) -> dict:
    """0~100 점. 규격 55 + 품질 권고 15 + 링크 15 + 스크립트 15.

    안전 신호는 오탐이 섞이는 "검토 위치 표시"라서 점수에 넣지 않고 따로 보고한다.
    """
    n = max(counts["skills"], 1)
    spec = 55 * counts["spec_pass"] / n
    quality = 15 * counts["clean"] / n
    links = 15 * (1 - counts["links_broken"] / counts["links_total"]) if counts["links_total"] else 15
    scripts = 15 * (1 - counts["scripts_bad"] / counts["scripts_total"]) if counts["scripts_total"] else 15
    total = round(spec + quality + links + scripts, 1)
    grade = "A" if total >= 90 else "B" if total >= 75 else "C" if total >= 60 else "D"
    return {"score": total, "grade": grade,
            "breakdown": {"spec": round(spec, 1), "quality": round(quality, 1),
                          "links": round(links, 1), "scripts": round(scripts, 1)}}
