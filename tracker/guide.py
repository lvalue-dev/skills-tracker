"""활용 가이드: 스킬이 무엇이고 언제 쓰는지 + 코딩 에이전트 CLI 별 적용 가능성.

내려받은 파일은 읽기만 한다 (실행 없음). 결과는 전부 정적 분석이다.

- 무엇/언제: SKILL.md description 을 "무엇을 하는가"와 "언제 쓰는가(트리거)"로 가른다.
- 분야: 이름·설명의 키워드 규칙으로 분류한다 (자동 분류라 틀릴 수 있다).
- CLI 호환: 레포에 들어 있는 패키징 파일(플러그인 매니페스트 등), 스킬이 놓인 위치,
  README 의 설치 안내, 특정 CLI 전용 기능 사용 여부를 근거로 판정한다.

CLI 별 사실(읽는 디렉터리·매니페스트·frontmatter)은 CLI_FACTS 의 공식 문서에서
확인한 내용이며 확인 날짜를 함께 적는다. CLI 가 바뀌면 이 표를 고친다.
"""
from __future__ import annotations

import json
import os
import posixpath
import re
from collections import Counter

import yaml

from .checks import read_text, split_frontmatter

FACTS_VERIFIED = "2026-11-19"

# 공식 문서 기준 (FACTS_VERIFIED 에 확인). dirs[0] 은 그 CLI 고유 위치, 나머지는 호환으로 읽는 위치.
CLI_FACTS = [
    {"id": "claude", "agent": "claude-code", "label": "Claude Code", "short": "Claude",
     "dirs": [".claude/skills"],
     "where": "`~/.claude/skills/` 또는 프로젝트 `.claude/skills/`",
     "package": "플러그인 `.claude-plugin/plugin.json` · 마켓플레이스 `.claude-plugin/marketplace.json`",
     "source": "https://code.claude.com/docs/en/skills"},
    {"id": "codex", "agent": "codex", "label": "Codex CLI", "short": "Codex",
     "dirs": [".agents/skills", ".codex/skills"],
     "where": "`~/.agents/skills/` 또는 프로젝트 `.agents/skills/` (`.claude/skills` 는 읽지 않음)",
     "package": "플러그인 `plugin.json`·`.codex-plugin/plugin.json` · 마켓플레이스 `.agents/plugins/marketplace.json`",
     "source": "https://developers.openai.com/codex/skills"},
    {"id": "opencode", "agent": "opencode", "label": "opencode", "short": "opencode",
     "dirs": [".opencode/skills", ".claude/skills", ".agents/skills"],
     "where": "`~/.config/opencode/skills/` 또는 `.opencode/skills/` (`.claude/skills`·`.agents/skills` 도 읽음)",
     "package": "스킬 묶음 형식은 문서에 없음 (`.opencode/`, `opencode.json` 설정)",
     "source": "https://opencode.ai/docs/skills/"},
    {"id": "kimi", "agent": "kimi-code-cli", "label": "Kimi Code CLI", "short": "Kimi",
     "dirs": [".kimi/skills", ".claude/skills", ".codex/skills", ".agents/skills"],
     "where": "`~/.kimi/skills/` 또는 `.kimi/skills/` (`.claude/skills`·`.codex/skills`·`.agents/skills` 도 읽음)",
     "package": "플러그인은 루트 `plugin.json` (스킬 묶음 여부는 문서에 없음)",
     "source": "https://moonshotai.github.io/kimi-cli/en/customization/skills.html"},
    {"id": "gemini", "agent": "gemini-cli", "label": "Gemini CLI", "short": "Gemini",
     "dirs": [".gemini/skills", ".agents/skills"],
     "where": "`~/.gemini/skills/` 또는 `.gemini/skills/` (`.agents/skills` 도 읽음, `.claude/skills` 는 읽지 않음)",
     "package": "확장 `gemini-extension.json` · `gemini skills install <git-url>`",
     "source": "https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/skills.md"},
    {"id": "cursor", "agent": "cursor", "label": "Cursor", "short": "Cursor",
     "dirs": [".cursor/skills", ".agents/skills", ".claude/skills", ".codex/skills"],
     "where": "`.cursor/skills/` 또는 `.agents/skills/` (`.claude/skills`·`.codex/skills` 도 읽음)",
     "package": "플러그인 `.cursor-plugin/plugin.json`",
     "source": "https://cursor.com/docs/context/skills"},
]
CLI_IDS = [c["id"] for c in CLI_FACTS]
CLI_LABEL = {c["id"]: c["label"] for c in CLI_FACTS}
CLI_SHORT = {c["id"]: c["short"] for c in CLI_FACTS}
LEVEL_LABEL = {"native": "전용 지원", "ok": "적용 가능", "limited": "일부 제한"}
LEVEL_MARK = {"native": "●", "ok": "○", "limited": "△"}
LEVEL_HELP = {
    "native": "레포에 그 CLI 용 패키징(플러그인·확장 매니페스트, 전용 스킬 폴더)이나 README 설치 안내가 있음",
    "ok": "전용 패키징은 없지만 표준 SKILL.md 라 그 CLI 의 스킬 폴더에 넣으면 인식됨",
    "limited": "넣으면 인식되지만 스킬의 1/4 이상이 다른 CLI 전용 기능에 기대고 있어 손봐야 할 수 있음",
}

# 레포에 이 파일/폴더가 있으면 그 CLI 용 패키징을 직접 실었다는 뜻. (정규식, CLI, 점수, 설명)
MARKERS = [
    (r"\.claude-plugin/plugin\.json", "claude", 3, "플러그인 매니페스트"),
    (r"\.claude-plugin/marketplace\.json", "claude", 2, "플러그인 마켓플레이스"),
    (r"CLAUDE\.md", "claude", 1, "CLAUDE.md"),
    (r"\.codex-plugin/plugin\.json", "codex", 3, "플러그인 매니페스트"),
    (r"\.agents/plugins/marketplace\.json", "codex", 2, "플러그인 마켓플레이스"),
    (r"\.codex/[^/]+", "codex", 1, "`.codex/` 설정"),
    (r"(?:.+/)?agents/openai\.yaml", "codex", 0, "스킬별 `agents/openai.yaml`"),
    (r"\.opencode/(?:plugins?|skills?|commands?|agents?)/.+", "opencode", 3, "`.opencode/` 구성"),
    (r"\.opencode/[^/]+", "opencode", 2, "`.opencode/` 설치 안내"),
    (r"opencode\.jsonc?", "opencode", 2, "opencode.json"),
    (r"\.kimi-plugin/plugin\.json", "kimi", 3, "`.kimi-plugin/` 매니페스트"),
    (r"\.kimi(?:-code)?/.+", "kimi", 3, "`.kimi/` 구성"),
    (r"gemini-extension\.json", "gemini", 3, "확장 매니페스트"),
    (r"GEMINI\.md", "gemini", 1, "GEMINI.md"),
    (r"\.gemini/.+", "gemini", 1, "`.gemini/` 구성"),
    (r"\.cursor-plugin/plugin\.json", "cursor", 3, "플러그인 매니페스트"),
    (r"\.cursor/rules/.+", "cursor", 2, "`.cursor/rules` 규칙"),
    (r"\.cursorrules", "cursor", 1, ".cursorrules"),
]
MARKERS = [(re.compile(rx), cli, pts, label) for rx, cli, pts, label in MARKERS]

README_NAME = {
    "claude": re.compile(r"\bclaude[ -]code\b", re.I),
    "codex": re.compile(r"\bcodex\b", re.I),
    "opencode": re.compile(r"\bopencode\b", re.I),
    "kimi": re.compile(r"\bkimi\b", re.I),
    "gemini": re.compile(r"\bgemini[ -]cli\b", re.I),
    "cursor": re.compile(r"\bCursor\b(?![-\w])"),
}
INSTALL_CMD = [
    ("claude", re.compile(r"^(?:/plugin|claude plugin) (?:marketplace add|install) \S+")),
    ("codex", re.compile(r"^codex plugin (?:marketplace add|add|install) \S+")),
    ("gemini", re.compile(r"^gemini (?:extensions|skills) install \S+")),
    ("kimi", re.compile(r"^kimi plugin install \S+")),
    ("any", re.compile(r"^npx (?:-y )?skills(?:@[\w.]+)? add \S+")),
    ("tool", re.compile(r"^npx (?:-y )?@?[\w./-]+(?:@[\w.]+)?(?: (?:setup|install|init)\b.*)?$")),
]
# 레포 자체 설치 도구의 대상 지정 옵션 (예: `uipro init --ai kimi`, `… install --target codex`)
OPTION_RE = re.compile(r"--(?:ai|target|agent|tool|ide|platform)[ =]([a-z][a-z-]+)|(?<!\S)-a ([a-z][a-z-]+)")
OPTION_CLI = {"claude": "claude", "claude-code": "claude", "codex": "codex", "opencode": "opencode",
              "kimi": "kimi", "kimi-code": "kimi", "kimi-code-cli": "kimi", "gemini": "gemini",
              "gemini-cli": "gemini", "cursor": "cursor"}
COMMAND_LINE = re.compile(r"[a-z][\w@./-]*(?: [\w@./:=<>\"'-]+){1,10}")
BEST_RE = re.compile(r"(?:works? best (?:with|in|on)|built for|designed for|optimi[sz]ed for|"
                     r"primarily (?:for|targets?))\s+(?:\*\*|`)?([A-Za-z][A-Za-z -]{2,24})", re.I)
CMD_SAFE = re.compile(r"[^A-Za-z0-9 @/.:_+=&<>~\"'\[\]-]")

# frontmatter 확장 키 → 그 키를 처리한다고 문서에 적힌 CLI. hard=True 면 없을 때 동작이 달라진다.
FM_EXT = {
    "when_to_use": (("claude",), False), "argument-hint": (("claude",), False),
    "arguments": (("claude",), True), "user-invocable": (("claude",), False),
    "disable-model-invocation": (("claude", "opencode", "cursor"), False),
    "disallowed-tools": (("claude",), False), "model": (("claude",), False),
    "effort": (("claude",), False), "context": (("claude",), True), "agent": (("claude",), True),
    "background": (("claude",), False), "hooks": (("claude",), True),
    "paths": (("claude", "cursor"), False), "shell": (("claude",), False),
    "globs": (("cursor",), False), "icon": (("cursor",), False), "color": (("cursor",), False),
}
# 본문 토큰 — Claude Code 문서의 치환 변수·도구 이름. 다른 CLI 문서에는 없다.
BODY_EXT = [
    ("$ARGUMENTS", re.compile(r"\$ARGUMENTS\b"), True),
    ("${CLAUDE_SKILL_DIR}", re.compile(r"CLAUDE_SKILL_DIR"), True),
    ("${CLAUDE_PLUGIN_ROOT}", re.compile(r"CLAUDE_PLUGIN_(?:ROOT|DATA)"), True),
    ("TodoWrite", re.compile(r"\bTodoWrite\b"), True),
    ("AskUserQuestion", re.compile(r"\bAskUserQuestion\b"), True),
    ("subagent_type", re.compile(r"\bsubagent_type\b"), True),
    ("~/.claude 경로", re.compile(r"~/\.claude/"), False),
]

# (id, 라벨, 이럴 때, 정규식) — 이름·설명 키워드로 고르는 자동 분류.
CATEGORIES = [
    ("plan", "기획·설계", "구현 전에 요구사항을 정리하고 설계·계획부터 세우고 싶을 때",
     r"plan(?:s|ning)?|brainstorm\w*|spec(?:s|ification)?|requirements?|architecture|prd|roadmap|grill\w*|blueprint|design doc"),
    ("test", "테스트·TDD", "테스트를 먼저 쓰거나 테스트·E2E 를 보강할 때",
     r"tdd|test(?:s|ing)?|test-driven|e2e|playwright|pytest|jest|coverage|red-green"),
    ("debug", "디버깅", "버그·테스트 실패의 원인을 체계적으로 추적할 때",
     r"debug\w*|bugs?|root cause|diagnos\w+|troubleshoot\w*|regressions?|incident"),
    ("review", "코드 리뷰·검증", "작업을 끝내기 전에 코드 리뷰·검증을 거치고 싶을 때",
     r"code review|review(?:s|ing|er)?|verif(?:y|ies|ication)|audit\w*|lint\w*"),
    ("refactor", "리팩터링·코드 품질", "동작은 그대로 두고 구조를 정리하거나 과한 코드를 줄일 때",
     r"refactor\w*|clean code|simplif\w+|tech(?:nical)? debt|dead code|overcomplicat\w+|smallest change|yagni"),
    ("git", "Git·PR·릴리스", "커밋·브랜치·PR·릴리스 노트 작업을 맡길 때",
     r"git|commits?|pull request|prs?|branch(?:es)?|worktrees?|merge|release|changelog"),
    ("office", "오피스 문서", "PDF·Word·Excel·PPT 파일을 만들거나 고칠 때",
     r"pdfs?|docx|xlsx|pptx|word doc\w*|spreadsheets?|powerpoint|slides?|excel|presentations?"),
    ("docs", "문서·글쓰기", "문서·글·보고서를 쓰거나 다듬을 때",
     r"documentation|docs|readme|writing|article|blog|copywriting|draft|newsletter|content"),
    ("frontend", "프런트엔드·UI 디자인", "화면·컴포넌트·디자인 시스템을 만들거나 다듬을 때",
     r"frontend|front-end|ui|ux|css|tailwind|react|vue|svelte|next\.?js|design system|landing page|"
     r"components?|typography|brand\w*|logo|banner|accessibility|interfaces?|styling"),
    ("backend", "백엔드·API·DB", "API·데이터베이스·서버 코드를 설계하거나 옮길 때",
     r"backend|apis?|rest|graphql|databases?|sql|postgres\w*|orm|migrations?|schema|microservices?|django|fastapi|spring"),
    ("devops", "배포·인프라", "배포·CI·컨테이너·클라우드 설정을 다룰 때",
     r"deploy\w*|docker|kubernetes|k8s|ci/cd|ci|pipelines?|terraform|infrastructure|aws|gcp|azure|"
     r"observability|monitoring|cluster"),
    ("security", "보안", "취약점 점검·비밀 정보 관리 등 보안 검토가 필요할 때",
     r"security|vulnerab\w+|secrets?|owasp|threat\w*|pentest\w*|injection|hardening"),
    ("reverse", "리버스 엔지니어링", "소스 없는 앱·바이너리의 동작을 분석할 때",
     r"reverse engineer\w*|binar(?:y|ies)|decompil\w+|firmware|disassembl\w+"),
    ("data", "데이터·AI/ML", "데이터 분석·RAG·모델 관련 작업을 할 때",
     r"data analysis|data science|analytics|machine learning|ml|training|pandas|datasets?|statistics|"
     r"rag|embeddings?|llm apps?|fine-tun\w+"),
    ("agent", "에이전트·스킬·MCP", "스킬·서브에이전트·MCP 서버를 만들거나 에이전트 작업을 나눠 맡길 때",
     r"skill creat\w+|creating (?:new )?skills|writing skills|skills?|subagents?|multi-agent|parallel agents|"
     r"orchestrat\w+|mcp|prompt engineering|hooks?|slash commands?|memory|context window|handoff"),
    ("token", "토큰·비용 절감", "답변·컨텍스트를 줄여 토큰과 비용을 아끼고 싶을 때",
     r"tokens?|cost|terse|concise|compress\w*|brief|spend"),
    ("research", "리서치·학습", "자료 조사나 코드베이스 파악·학습이 필요할 때",
     r"research\w*|investigat\w+|explor\w+|teach|learn\w*|onboarding|walkthroughs?|tutorial"),
    ("media", "이미지·영상", "이미지·영상·아트 같은 시각 자료를 만들 때",
     r"images?|videos?|audio|gif|art|canvas|animation|illustrations?|music|poster"),
    ("biz", "비즈니스·마케팅", "마케팅·SEO·영업·사내 커뮤니케이션 문서를 다룰 때",
     r"marketing|seo|sales|investor|pitch|business|finance|legal|hiring|customer|internal comm\w+|"
     r"email|social media|twitter|linkedin"),
    ("pm", "이슈·프로젝트 관리", "이슈 분류·티켓 작성·작업 인계를 정리할 때",
     r"issues?|triage|tickets?|jira|linear|backlog|retro\w*|standup"),
]
CATEGORIES = [(cid, label, when, re.compile(r"(?<![\w-])(?:" + rx + r")(?![\w-])", re.I))
              for cid, label, when, rx in CATEGORIES]
CAT_LABEL = {cid: label for cid, label, _, _ in CATEGORIES}
CAT_WHEN = {cid: when for cid, _, when, _ in CATEGORIES}
CAT_LABEL["etc"] = "기타"

TRIGGER_RE = re.compile(
    r"\b(?:you (?:must|should) )?use (?:this (?:skill )?|it |proactively )?"
    r"(?:when(?:ever)?|for|if|on|before|after|any time|to)\b"
    r"|\b(?:this skill )?should be used (?:when(?:ever)?|for)\b"
    r"|\bclaude should use this skill\b"
    r"|\btriggers?(?: include| on| when)?\s*[:—–-]"
    r"|\bTRIGGER\b"
    r"|\binvoke (?:only )?(?:when|with|for)\b"
    r"|\bactivate (?:when|for)\b", re.I)
LOCALES = {"ja", "zh", "ko", "es", "fr", "de", "pt", "ru", "tr", "it", "vi", "id", "ar", "hi", "th",
           "pl", "nl", "uk", "fa", "az", "he", "el", "ro", "bg", "ml", "ur"}
LOCALE_RE = re.compile(r"[a-z]{2}[-_][A-Za-z]{2,4}")
SIDE_DIRS = {"docs", "i18n", "translations", "locales", "examples", "example", "tests", "test",
             "fixtures", "assets", "templates", "template", "archive", "deprecated"}


def clip(text: str | None, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def split_usage(description: str | None) -> tuple[str, str]:
    """description 을 (무엇을 하는가, 언제 쓰는가) 로 가른다. 트리거 문구가 없으면 when 은 빈 문자열."""
    desc = " ".join((description or "").split())
    match = TRIGGER_RE.search(desc)
    if not match:
        return desc, ""
    what = desc[: match.start()].rstrip(" ;,:—–-(")
    return what, desc[match.start():].strip()


def first_paragraph(body: str) -> str:
    """본문의 첫 설명 문단 (제목·목록·코드·표·HTML 제외)."""
    in_fence = False
    lines: list[str] = []
    for raw in (body or "").splitlines():
        line = raw.strip()
        if line.startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not line or line[0] in "#|<>-![" or re.match(r"(?:[*+] |\d+\.)", line):
            if lines:
                break
            continue
        lines.append(line)
    return " ".join(lines).replace("**", "")


def categorize(name: str, description: str) -> list[str]:
    """이름(가중 3)·설명(일치 수) 키워드로 분야를 최대 2개 고른다."""
    name_text = (name or "").replace("-", " ").replace("_", " ")
    scored = []
    for order, (cid, _, _, regex) in enumerate(CATEGORIES):
        points = 3 * min(len(regex.findall(name_text)), 1) + min(len(regex.findall(description or "")), 3)
        if points:
            scored.append((-points, order, cid))
    scored.sort()
    if not scored:
        return ["etc"]
    picked = [scored[0][2]]
    if len(scored) > 1 and scored[1][0] <= -2 and scored[1][0] * 10 <= scored[0][0] * 6:
        picked.append(scored[1][2])
    return picked


def skill_features(meta: dict | None, text: str) -> list[dict]:
    """특정 CLI 문서에만 있는 기능 사용 흔적. [{"key", "clis", "hard"}]"""
    found = []
    for key, (clis, hard) in FM_EXT.items():
        if isinstance(meta, dict) and key in meta:
            found.append({"key": key, "clis": list(clis), "hard": hard})
    if isinstance(meta, dict) and str(meta.get("type", "")).strip().lower() == "flow":
        found.append({"key": "type: flow", "clis": ["kimi"], "hard": True})
    for key, regex, hard in BODY_EXT:
        if regex.search(text or ""):
            found.append({"key": key, "clis": ["claude"], "hard": hard})
    return found


def _side_penalty(path: str) -> int:
    """번역본·어댑터 복사본·예제 폴더일수록 큰 값 (대표 경로를 고를 때 뒤로 미룬다)."""
    parts = path.split("/")[:-1]
    if any(p in LOCALES or LOCALE_RE.fullmatch(p) for p in parts) or (parts and parts[0] == "docs"):
        return 3
    if any(p in SIDE_DIRS for p in parts):
        return 2
    return 1 if parts and parts[0].startswith(".") else 0


def parse_skill(skill_dir: str, text: str) -> dict:
    front, body = split_frontmatter(text or "")
    meta = None
    if front is not None:
        try:
            meta = yaml.safe_load(front)
        except yaml.YAMLError:
            meta = None
    if not isinstance(meta, dict):
        meta = None
    raw_name = meta.get("name") if meta else None
    name = raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else \
        (posixpath.basename(skill_dir) or "(root)")
    raw_desc = meta.get("description") if meta else None
    desc = raw_desc if isinstance(raw_desc, str) else ""
    extra = meta.get("when_to_use") if meta else None
    what, when = split_usage(desc)
    if isinstance(extra, str) and extra.strip():
        when = (when + " " + " ".join(extra.split())).strip()
    if not what:
        what = first_paragraph(body)
    return {"dir": skill_dir, "name": name, "what": what, "when": when,
            "cats": categorize(name, desc), "features": skill_features(meta, text or ""),
            "deprecated": desc.lstrip("* ").upper().startswith("DEPRECATED")}


def unique_skills(parsed: list[dict]) -> list[dict]:
    """같은 이름의 복사본(번역·CLI 별 어댑터)을 하나로 합쳐 대표 경로만 남긴다."""
    roots = Counter(posixpath.dirname(s["dir"]) for s in parsed)
    best: dict[str, dict] = {}
    copies: Counter = Counter()
    for skill in parsed:
        key = (_side_penalty(skill["dir"]), -roots[posixpath.dirname(skill["dir"])],
               skill["dir"].count("/"), skill["dir"])
        copies[skill["name"]] += 1
        if skill["name"] not in best or key < best[skill["name"]]["_key"]:
            best[skill["name"]] = {**skill, "_key": key}
    out = []
    for name, skill in best.items():
        skill = {k: v for k, v in skill.items() if k != "_key"}
        skill["copies"] = copies[name]
        out.append(skill)
    out.sort(key=lambda s: s["dir"])
    return out


def pick_skills(skills: list[dict], limit: int) -> list[dict]:
    """분야가 고루 보이도록 돌아가며 limit 개를 고른다."""
    groups: dict[str, list[dict]] = {}
    for skill in sorted(skills, key=lambda s: (s["deprecated"], not s["when"], not s["what"], s["dir"])):
        groups.setdefault(skill["cats"][0], []).append(skill)
    order = sorted(groups, key=lambda c: (-len(groups[c]), c))
    picked: list[dict] = []
    while len(picked) < limit and any(groups[c] for c in order):
        for cat in order:
            if groups[cat] and len(picked) < limit:
                picked.append(groups[cat].pop(0))
    return picked


def scan_readme(readme: str | None) -> dict:
    """README 에서 CLI 언급 횟수·전용 설치 절(제목)·설치 명령을 찾는다."""
    mentions: Counter = Counter()
    headings: set = set()
    title: set = set()
    best: set = set()
    options: dict[str, dict] = {}
    commands: list[dict] = []
    seen_h1 = False
    for raw in (readme or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        is_heading = line.startswith("#") or "<summary" in line
        for cli, regex in README_NAME.items():
            hits = len(regex.findall(line))
            if hits:
                mentions[cli] += hits
                if is_heading:
                    headings.add(cli)
                    if line.startswith("# ") and not seen_h1:
                        title.add(cli)
        if line.startswith("# "):
            seen_h1 = True
        for match in BEST_RE.finditer(line):
            for cli, regex in README_NAME.items():
                if regex.match(match.group(1)):
                    best.add(cli)
        cmd = re.sub(r"^(?:[-*]\s+|\$\s+)", "", line)
        for match in OPTION_RE.finditer(line):
            value = match.group(1) or match.group(2)
            if value in OPTION_CLI and OPTION_CLI[value] not in options:
                plain = cmd.split(" #", 1)[0].strip().strip("`")
                options[OPTION_CLI[value]] = {
                    "opt": match.group(0).strip(),
                    "cmd": plain[:140] if COMMAND_LINE.fullmatch(plain) else ""}
        for cli, regex in INSTALL_CMD:
            if regex.match(cmd) and len(commands) < 12:
                cmd = CMD_SAFE.sub("", cmd.split(" #", 1)[0])[:140].strip()
                if not any(c["cmd"] == cmd for c in commands) and \
                        sum(1 for c in commands if c["cli"] == cli) < 2:
                    commands.append({"cli": cli, "cmd": cmd})
                break
    return {"mentions": dict(mentions), "headings": sorted(headings), "title": sorted(title),
            "best": sorted(best), "options": options, "commands": commands}


def repo_kind(unique: int, tree: dict, skill_dirs: list[str], repo: dict) -> str:
    prefixes = tuple(d + "/" for d in skill_dirs if d)
    in_skills = len(tree) if "" in skill_dirs else sum(1 for p in tree if p.startswith(prefixes))
    share = in_skills / max(len(tree), 1)
    about = " ".join([repo.get("full_name") or "", repo.get("description") or ""]
                     + list(repo.get("topics") or [])).lower()
    if share < 0.06 and len(tree) > 200 and "skill" not in about:
        return "앱·도구 레포 (스킬은 일부)"
    if unique == 1:
        return "단일 스킬"
    if unique <= 8:
        return "소규모 스킬 세트"
    return "대형 스킬 모음" if unique > 60 else "스킬 모음"


def compat(tree: dict, skills: list[dict], readme_scan: dict, repo: dict) -> dict:
    """CLI 별 적용 수준과 '어디에 최적화됐는지'를 근거와 함께 돌려준다."""
    n = max(len(skills), 1)
    score = {cli: 0.0 for cli in CLI_IDS}
    evidence: dict[str, list[str]] = {cli: [] for cli in CLI_IDS}
    native = {cli: False for cli in CLI_IDS}

    def add(cli: str, points: float, text: str, is_native: bool = False) -> None:
        score[cli] += points
        if text and text not in evidence[cli]:
            evidence[cli].append(text)
        native[cli] = native[cli] or is_native

    # 1) 패키징 파일
    seen = set()
    sidecars = 0
    for path in tree:
        for index, (regex, cli, points, label) in enumerate(MARKERS):
            if regex.fullmatch(path):
                if label.endswith("openai.yaml`"):
                    sidecars += 1
                elif index not in seen:
                    seen.add(index)
                    add(cli, points, label, is_native=points >= 2)
    if sidecars:
        add("codex", min(3.0, 3 * sidecars / n), f"스킬별 `agents/openai.yaml` {sidecars}개", True)

    # 2) 스킬이 놓인 위치
    for fact in CLI_FACTS:
        for index, base in enumerate(fact["dirs"]):
            count = sum(1 for s in skills if s["dir"].startswith(base + "/"))
            if not count:
                continue
            if index == 0:
                add(fact["id"], 2, f"스킬이 `{base}/` 에 있음 ({count}개)", True)
            else:
                add(fact["id"], 0, f"레포의 `{base}/` 를 그대로 읽음 ({count}개)")

    # 3) README
    for item in readme_scan["commands"]:
        if item["cli"] in score:
            add(item["cli"], 2, "README 에 설치 명령", True)
    for cli in readme_scan["headings"]:
        add(cli, 1, "README 에 전용 설치 절", True)
    for cli, option in readme_scan["options"].items():
        add(cli, 2, f"자체 설치 도구의 `{option['opt']}` 옵션", True)
    for cli in readme_scan["best"]:
        add(cli, 3, "README 가 주 대상으로 명시")
    for cli in readme_scan["title"]:
        add(cli, 2, "README 제목에 명시")
    total_mentions = sum(readme_scan["mentions"].values())
    for cli, count in readme_scan["mentions"].items():
        if total_mentions >= 3:
            add(cli, round(3 * count / total_mentions, 1), "")
        if cli not in readme_scan["headings"] and not native[cli]:
            add(cli, 0, f"README 에서 언급 ({count}회)")
    about = " ".join([repo.get("description") or ""] + list(repo.get("topics") or []))
    for cli, regex in README_NAME.items():
        if regex.search(about.replace("-", " ")) or regex.search(about):
            add(cli, 1, "레포 설명·토픽에 명시")

    # 4) 특정 CLI 전용 기능
    feature_count: Counter = Counter()
    feature_meta: dict[str, dict] = {}
    hard_users: dict[str, int] = {cli: 0 for cli in CLI_IDS}   # 그 CLI 에서 안 되는 기능을 쓰는 스킬 수
    own_users: Counter = Counter()
    portable = 0
    for skill in skills:
        owners = set()
        blocked = set()
        for feat in skill["features"]:
            feature_count[feat["key"]] += 1
            feature_meta[feat["key"]] = feat
            owners.update(feat["clis"][:1])
            if feat["hard"]:
                blocked.update(c for c in CLI_IDS if c not in feat["clis"])
        for cli in owners:
            own_users[cli] += 1
        for cli in blocked:
            hard_users[cli] += 1
        if not any(f["hard"] for f in skill["features"]):
            portable += 1
    for cli, count in own_users.items():
        add(cli, round(3 * count / n, 1), f"{CLI_LABEL[cli]} 전용 기능을 쓰는 스킬 {count}개")

    clis = []
    for fact in CLI_FACTS:
        cli = fact["id"]
        caveats = []
        if hard_users[cli]:
            caveats.append(f"다른 CLI 전용 기능을 쓰는 스킬 {hard_users[cli]}개 — 그대로는 일부 동작이 다를 수 있음")
        if native[cli]:
            level = "native"
        elif hard_users[cli] / n >= 0.25:
            level = "limited"
        else:
            level = "ok"
        how = [c["cmd"] for c in readme_scan["commands"] if c["cli"] == cli]
        how_kind = "readme"
        if not how and readme_scan["options"].get(cli, {}).get("cmd"):
            how = [readme_scan["options"][cli]["cmd"]]
        if not how and native[cli]:
            how_kind = "section"      # 전용 패키징은 있는데 한 줄 명령은 못 찾음 → README 안내를 보라고 한다
        elif not how:
            how_kind = "generic"
            full_name = repo.get("full_name") or "owner/repo"
            how = [f"npx skills add {full_name} -a {fact['agent']}"]
        clis.append({"id": cli, "label": fact["label"], "level": level,
                     "score": round(score[cli], 1), "evidence": evidence[cli][:5], "caveats": caveats,
                     "how": how[:2], "how_kind": how_kind})

    # 최적화 대상: 근거 점수가 가장 높은 CLI, 그리고 그 60% 이상인 CLI 들
    ranked = sorted(clis, key=lambda c: -c["score"])
    best = ranked[0]
    if best["score"] < 4:
        kind, optimized = "none", []
    else:
        optimized = [c["id"] for c in ranked if c["score"] >= 0.6 * best["score"] and c["score"] >= 4]
        kind = "single" if len(optimized) == 1 else "multi"
    if kind == "none":
        label = "특정 CLI 없음 (표준 SKILL.md)"
    elif kind == "single":
        label = CLI_LABEL[optimized[0]]
    else:
        label = "멀티 CLI — " + " · ".join(CLI_SHORT[c] for c in optimized)
    features = [{"key": key, "count": count, "clis": feature_meta[key]["clis"],
                 "hard": feature_meta[key]["hard"]}
                for key, count in feature_count.most_common(8)]
    return {"clis": clis, "optimized": optimized, "optimized_kind": kind, "optimized_label": label,
            "portable": {"clean": portable, "total": len(skills)}, "features": features,
            "verified": FACTS_VERIFIED}


_NOTES: dict | None = None


def load_notes() -> dict:
    """사람이 정리한 한글 요약 (tracker/notes_ko.json). 없는 레포는 자동 분석만 쓴다."""
    global _NOTES
    if _NOTES is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "notes_ko.json")
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            _NOTES = {"updated": data.get("updated"),
                      "repos": {k.lower(): v for k, v in data.get("repos", {}).items()}}
        except (OSError, ValueError):
            _NOTES = {"updated": None, "repos": {}}
    return _NOTES


def note_for(full_name: str) -> dict | None:
    notes = load_notes()
    note = notes["repos"].get((full_name or "").lower())
    return {**note, "source": "hand", "updated": notes["updated"]} if note else None


def meta() -> dict:
    """스냅샷에 함께 남기는 판정 기준 — 화면이 그날 기준의 CLI 정보를 그대로 보여 줄 수 있게 한다."""
    return {"verified": FACTS_VERIFIED, "notes_updated": load_notes().get("updated"),
            "levels": [{"id": k, "mark": LEVEL_MARK[k], "label": LEVEL_LABEL[k], "help": LEVEL_HELP[k]}
                       for k in ("native", "ok", "limited")],
            "clis": [{k: c[k] for k in ("id", "label", "short", "where", "package", "source")} for c in CLI_FACTS]}


def build_from(tree: dict, skill_texts: dict[str, str], readme: str | None, repo: dict,
               max_listed: int = 30) -> dict:
    """순수 함수: 트리·SKILL.md 본문·README 텍스트에서 활용 가이드를 만든다."""
    parsed = [parse_skill(d, text) for d, text in skill_texts.items()]
    skills = unique_skills(parsed)
    cat_count = Counter(s["cats"][0] for s in skills)
    cats = [{"id": cid, "label": CAT_LABEL[cid], "count": count}
            for cid, count in cat_count.most_common(6)]
    situations = [CAT_WHEN[c["id"]] for c in cats if c["id"] in CAT_WHEN][:3]
    readme_scan = scan_readme(readme)
    listed = [{"name": clip(s["name"], 64), "path": s["dir"], "what": clip(s["what"], 200),
               "when": clip(s["when"], 240), "cats": s["cats"],
               "features": [f["key"] for f in s["features"]][:6]}
              for s in pick_skills(skills, max_listed)]
    return {"kind": repo_kind(len(skills), tree, list(skill_texts), repo),
            "unique_skills": len(skills), "copies": len(parsed) - len(skills),
            "categories": cats, "situations": situations, "skills": listed,
            "install": readme_scan["commands"][:8],
            "compat": compat(tree, skills, readme_scan, repo),
            "note": note_for(repo.get("full_name", ""))}


def build_guide(root: str, fetched: dict, repo: dict, limits: dict, summarizer=None) -> dict:
    """체크아웃된 레포에서 SKILL.md·README 를 읽어 활용 가이드를 만든다.

    사람이 쓴 요약이 없는 레포는 `summarizer(repo, guide, readme)` 가 있으면 그것으로 요약을 채운다.
    """
    max_bytes = limits["max_file_bytes"]
    texts = {}
    for d in fetched["skill_dirs"]:
        rel = posixpath.join(d, "SKILL.md") if d else "SKILL.md"
        texts[d] = read_text(os.path.join(root, rel), max_bytes * 2) or ""
    readme = None
    if fetched.get("readme"):
        readme = read_text(os.path.join(root, fetched["readme"]), max_bytes)
    result = build_from(fetched["tree"], texts, readme, repo)
    if result["note"] is None and summarizer is not None:
        result["note"] = summarizer(repo, result, readme)
    return result
