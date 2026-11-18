#!/usr/bin/env bash
# 일일 실행 진입점: 한글 폰트·의존성 확인 → 검사기 자체 테스트 → 수집·테스트·리포트.
# 인자는 그대로 `python -m tracker` 에 전달된다 (예: --date 2026-11-18, --render-only).
set -euo pipefail
cd "$(dirname "$0")/.."

if ! fc-list :lang=ko 2>/dev/null | grep -q .; then
  SUDO=""
  if [ "$(id -u)" -ne 0 ]; then SUDO="sudo"; fi
  $SUDO apt-get update -qq
  $SUDO apt-get install -y -qq --no-install-recommends fonts-noto-cjk
fi
python3 -c "import yaml" 2>/dev/null || python3 -m pip install -q -r requirements.txt

python3 -m unittest discover -s tests
python3 -m tracker "$@"
