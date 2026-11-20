"""오늘의 요약: 전일 스냅샷과 비교해 사람이 바로 읽을 핵심만 뽑는다.

결과는 데이터(dict)로만 만들고, 문장으로 바꾸는 일은 report.py 가 한다.
그래서 PDF·Markdown·웹이 같은 내용을 같은 말투로 보여준다.
"""
from __future__ import annotations

from . import guide

MAX_CLIMBERS = 5


def _blurb(full_name: str, description: str) -> str:
    """한 줄 설명: 사람이 쓴 한글 요약이 있으면 그 첫 문장, 없으면 GitHub 설명."""
    note = guide.note_for(full_name) or {}
    summary = (note.get("summary") or "").strip()
    if summary:
        return summary.split(". ")[0].rstrip(".") if ". " in summary else summary
    return " ".join((description or "").split())


def _error_kinds(test: dict) -> dict[str, int]:
    return {g["kind"]: g["count"] for g in test.get("issue_summary", []) if g["level"] != "warning"}


def _high_flags(test: dict) -> dict[str, dict]:
    return {f["rule"]: f for f in test.get("flag_summary", []) if f["severity"] == "high"}


def _avg(top: list[dict]) -> float | None:
    scores = [e["test"]["score"] for e in top]
    return round(sum(scores) / len(scores), 1) if scores else None


def build(snapshot: dict, previous: dict | None) -> dict:
    top = snapshot["top"]
    prev_top = {e["full_name"]: e for e in (previous or {}).get("top", [])}
    has_prev = bool(previous)

    moved = [{"full_name": e["full_name"], "rank": e["rank"], "prev_rank": e.get("prev_rank")}
             for e in top if e.get("prev_rank") is not None and e["prev_rank"] != e["rank"]]
    entered = [{"full_name": e["full_name"], "rank": e["rank"], "stars": e["stars"]}
               for e in top if has_prev and e.get("is_new")]
    left = ([{"full_name": n, "prev_rank": p["rank"]} for n, p in prev_top.items()
             if n not in {e["full_name"] for e in top}] if has_prev else [])
    left.sort(key=lambda x: x["prev_rank"])

    gains = [e for e in top if e.get("stars_delta") is not None]
    star_leader = max(gains, key=lambda e: e["stars_delta"], default=None)
    top_names = {e["full_name"] for e in top}
    outside = [r for r in snapshot.get("rising", []) if r["full_name"] not in top_names]

    climbers = [{**c, "blurb": _blurb(c["full_name"], c.get("description") or _desc(snapshot, c["full_name"]))}
                for c in snapshot.get("climbers", [])[:MAX_CLIMBERS]]

    improved, worsened, skill_changes, resolved = [], [], [], []
    for e in top:
        p = prev_top.get(e["full_name"])
        if not p:
            continue
        now_t, prev_t = e["test"], p["test"]
        fixed = [k for k in _error_kinds(prev_t) if k not in _error_kinds(now_t)]
        added = [k for k in _error_kinds(now_t) if k not in _error_kinds(prev_t)]
        change = {"full_name": e["full_name"], "prev": prev_t["score"], "now": now_t["score"],
                  "fixed": fixed, "added": added}
        if now_t["score"] > prev_t["score"]:
            improved.append(change)
        elif now_t["score"] < prev_t["score"]:
            worsened.append(change)
        if fixed:
            resolved += [{"full_name": e["full_name"], "kind": k} for k in fixed]
        before, after = prev_t["counts"]["skills"], now_t["counts"]["skills"]
        if before != after:
            skill_changes.append({"full_name": e["full_name"], "prev": before, "now": after})

    errors = [{"full_name": e["full_name"], "kind": g["kind"], "count": g["count"], "example": g["example"],
               "level": g["level"]}
              for e in top for g in e["test"].get("issue_summary", []) if g["level"] != "warning"]
    flags = []
    for e in top:
        prev_high = _high_flags(prev_top[e["full_name"]]["test"]) if e["full_name"] in prev_top else None
        for rule, f in _high_flags(e["test"]).items():
            same = prev_high is not None and rule in prev_high and prev_high[rule]["path"] == f["path"]
            flags.append({"full_name": e["full_name"], "label": f["label"], "count": f["count"],
                          "path": f["path"], "line": f["line"],
                          "status": "same" if same else ("new" if prev_high is not None else "unknown")})

    return {
        "previous_date": previous.get("date") if previous else None,
        "top": {"moved": moved, "entered": entered, "left": left},
        "star_leader": ({"full_name": star_leader["full_name"], "delta": star_leader["stars_delta"]}
                        if star_leader else None),
        "outside_leader": ({"full_name": outside[0]["full_name"], "delta": outside[0]["delta"],
                            "stars": outside[0]["stars"],
                            "blurb": _blurb(outside[0]["full_name"], outside[0].get("description", ""))}
                           if outside else None),
        "climbers": climbers,
        "climbers_total": len(snapshot.get("climbers", [])),
        "pool_size": snapshot.get("pool_size"),
        "tests": {
            "avg": _avg(top), "avg_prev": _avg(list(prev_top.values())) if has_prev else None,
            "grades": _grades(top), "repos": len(top),
            "improved": improved, "worsened": worsened, "resolved": resolved,
            "skill_changes": skill_changes, "errors": errors, "high_flags": flags,
        },
        "excluded": [{"full_name": x["full_name"], "reason": x["reason"]} for x in snapshot.get("excluded", [])],
    }


def _desc(snapshot: dict, full_name: str) -> str:
    for r in snapshot.get("rising", []):
        if r["full_name"] == full_name:
            return r.get("description", "")
    for r in snapshot["top"] + snapshot.get("pool", []):
        if r["full_name"] == full_name and r.get("description"):
            return r["description"]
    return ""


def _grades(top: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for e in top:
        out[e["test"]["grade"]] = out.get(e["test"]["grade"], 0) + 1
    return out
