"""홈서버용 상주 스케줄러: 매일 정해진 시각에 scripts/run_daily.sh 를 실행한다.

    python -m tracker.schedule

- 실행 시각은 환경 변수 RUN_AT(HH:MM) 또는 config.json 의 run_at, 시간대는 config 의 timezone.
- 서버가 꺼져 있다가 실행 시각을 지나 켜지면, 오늘자 리포트가 없을 때 바로 한 번 실행한다.
- 실패하면 30분 간격으로 하루 최대 3번까지만 다시 시도한다 (상대 서버에 요청을 반복하지 않기 위함).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAX_ATTEMPTS_PER_DAY = 3
RETRY_SECONDS = 30 * 60
MAX_SLEEP_SECONDS = 15 * 60  # 절전·시계 변경에 대비해 짧게 끊어 잔다


def parse_run_at(text: str) -> dt.time:
    hour, minute = text.strip().split(":")
    return dt.time(int(hour), int(minute))


def next_action(now: dt.datetime, run_at: dt.time, report_exists: bool,
                attempts_today: int, last_attempt: dt.datetime | None) -> tuple[str, float]:
    """지금 할 일을 정한다: ("run", 0) 또는 ("sleep", 초)."""
    scheduled = now.replace(hour=run_at.hour, minute=run_at.minute, second=0, microsecond=0)
    tomorrow = (scheduled + dt.timedelta(days=1) - now).total_seconds()
    if now < scheduled:
        return "sleep", (scheduled - now).total_seconds()
    if report_exists or attempts_today >= MAX_ATTEMPTS_PER_DAY:
        return "sleep", tomorrow
    if last_attempt is not None:
        wait = RETRY_SECONDS - (now - last_attempt).total_seconds()
        if wait > 0:
            return "sleep", min(wait, tomorrow)
    return "run", 0.0


def log(message: str) -> None:
    print(f"[schedule] {message}", flush=True)


def main() -> int:
    with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as fh:
        cfg = json.load(fh)
    zone = ZoneInfo(cfg["timezone"])
    run_at = parse_run_at(os.environ.get("RUN_AT") or cfg.get("run_at", "08:23"))
    log(f"매일 {run_at:%H:%M} ({cfg['timezone']}) 실행")
    attempts: dict[str, int] = {}
    last_attempt: dt.datetime | None = None
    while True:
        now = dt.datetime.now(zone)
        today = now.strftime("%Y-%m-%d")
        exists = os.path.exists(os.path.join(ROOT, "reports", f"{today}.pdf"))
        action, seconds = next_action(now, run_at, exists, attempts.get(today, 0), last_attempt)
        if action == "sleep":
            time.sleep(max(1.0, min(seconds, MAX_SLEEP_SECONDS)))
            continue
        attempts = {today: attempts.get(today, 0) + 1}
        last_attempt = now
        log(f"{today} 리포트 생성 시작 (시도 {attempts[today]}/{MAX_ATTEMPTS_PER_DAY})")
        code = subprocess.run([os.path.join(ROOT, "scripts", "run_daily.sh"), "--date", today],
                              cwd=ROOT).returncode
        log(f"{today} 리포트 생성 {'완료' if code == 0 else f'실패 (종료 코드 {code})'}")


if __name__ == "__main__":
    sys.exit(main())
