# skills-tracker

AI 에이전트용 스킬(Agent Skills, `SKILL.md`) 레포를 GitHub 스타 순으로 매일 10개 수집하고,
자체 정적 테스트를 거쳐 일자별 PDF 리포트로 남깁니다.

- 리포트: [`reports/`](reports/README.md) — `YYYY-MM-DD.pdf` (같은 내용의 `.md` 포함)
- 원본 데이터: `data/YYYY-MM-DD.json` — 다음 날 스타 증감·순위 변동 계산에 사용

## 매일 하는 일

1. **수집** — 스킬 관련 토픽·이름으로 GitHub 검색(스타 내림차순) 후 후보 풀을 만든다.
2. **선별** — 스타 순으로 내려가며 실제로 `SKILL.md` 를 가진 레포 10개를 고른다.
3. **자체 테스트** — 스킬마다 다음을 정적으로 검사한다.
   - Agent Skills 공개 규격: frontmatter, `name` 형식, `description` 유무·길이
   - 품질 권고: `name`↔폴더명 일치, description 길이, 본문 500줄 이하
   - `SKILL.md` 안 상대 링크가 실제 파일을 가리키는지
   - 번들 스크립트 문법(Python `ast`, `bash -n`, `node --check`)
   - 위험 패턴 스캔(원격 스크립트 셸 실행, 전체 삭제, 난독화 실행, 숨김 유니코드 등)
4. **리포트** — 순위·전일 대비 스타 증감·급상승 레포·레포별 테스트 결과를 PDF 로 만든다.

점수는 규격 55 + 품질 권고 15 + 링크 15 + 스크립트 15 (A 90↑ · B 75↑ · C 60↑ · D).
규격 준수와 무결성을 잰 값이며 스킬의 실제 효용을 보증하지는 않습니다.
안전 신호는 사람이 확인할 위치 표시이고 오탐이 섞이므로 점수에 넣지 않습니다.

## 다른 레포를 건드리지 않는 원칙

- 다른 레포에는 **읽기만** 한다: 검색 API `GET` 과 공개 레포 클론뿐이며,
  star·fork·issue·PR·댓글 같은 쓰기 호출은 코드에 존재하지 않는다.
- 레포당 depth 1 · blob 없는 스파스 클론 1회로 스킬 폴더만 받고, 요청 사이에 간격을 둔다.
- 내려받은 코드는 **실행하지 않는다**. 문법 검사는 파싱만 한다(단위 테스트로 보장).
- 서브모듈·LFS·심볼릭 링크는 받지 않고, 토큰은 git·검사 프로세스에 전달하지 않는다.
- 클론은 검사 직후 삭제하고, 리포트에는 메타데이터와 검사 결과만 싣는다.
- 워크플로 권한은 이 레포에 대한 `contents: write` 하나뿐이다.

## 실행

자동: `.github/workflows/daily-report.yml` 이 매일 08:23 KST 에 실행되어 결과를 커밋합니다.
Actions 탭에서 **Run workflow** 로 수동 실행할 수도 있습니다.

수동(로컬):

```bash
pip install -r requirements.txt
python -m unittest discover -s tests   # 검사기 자체 테스트 (네트워크 불필요)
python -m tracker                      # 오늘자 수집·테스트·리포트
python -m tracker --render-only        # 저장된 data/<오늘>.json 으로 리포트만 재생성
```

필요한 것: Python 3.11+, `git`, `gh`(로그인 또는 `GH_TOKEN`), `node`, Chrome/Chromium
(`CHROME_BIN` 으로 지정 가능), 한글 폰트(`fonts-noto-cjk`).

## 설정

`config.json` 에서 검색어(`search_queries`), 수집 개수(`top_n`), 시간대, 요청 간격, 상한을 바꿀 수 있습니다.
