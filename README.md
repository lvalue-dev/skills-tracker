# skills-tracker

AI 에이전트용 스킬(Agent Skills, `SKILL.md`) 레포를 GitHub 스타 순으로 매일 10개 수집하고,
자체 정적 테스트를 거쳐 일자별 PDF 리포트로 남깁니다.

- 리포트: [`reports/`](reports/README.md) — `YYYY-MM-DD.pdf`, 같은 내용의 웹 페이지 `.html` 과 `.md`
- 매 리포트 맨 앞의 **오늘의 요약** 에 전일 대비 순위 변동(▲2, 35위→33위)·스타 증가·순위 상승 레포·
  점수 변화·해결되거나 새로 생긴 오류가 문장으로 정리됩니다 (PDF·웹·Markdown 동일).
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
4. **활용 가이드** — 레포마다 어떤 스킬인지, 언제 쓰는지, CLI 별 적용 방법을 정리한다 (`tracker/guide.py`).
   - 한글 요약은 `tracker/notes_ko.json` 에 사람이 쓴 것을 우선 쓰고, 없으면 (선택) LLM 의 ‘AI 요약’,
     그것도 없으면 `SKILL.md` description 과 키워드 분류로 만든 자동 분석을 보여 준다.
   - Claude Code · Codex · opencode · Kimi · Gemini CLI · Cursor 에 대해 전용 지원(●) / 적용 가능(○) /
     일부 제한(△) 과 ‘최적화 대상’을 표시한다. 레포 안의 매니페스트, 스킬 폴더 위치, README 설치 안내,
     특정 CLI 전용 기능 사용을 근거로 한 **추정**이며 각 CLI 에서 실행해 검증한 값이 아니다.
5. **리포트** — 순위·전일 대비 순위 변동 폭(예: ▲2, 5위→3위)과 스타 증감·급상승 및 순위 상승 레포·CLI 호환성 표·레포별 가이드와 테스트 결과를 PDF 로 만든다.

점수는 규격 55 + 품질 권고 15 + 링크 15 + 스크립트 15 (A 90↑ · B 75↑ · C 60↑ · D).
규격 준수와 무결성을 잰 값이며 스킬의 실제 효용을 보증하지는 않습니다.
안전 신호는 사람이 확인할 위치 표시이고 오탐이 섞이므로 점수에 넣지 않습니다.

## 다른 레포를 건드리지 않는 원칙

- 다른 레포에는 **읽기만** 한다: 검색 API `GET` 과 공개 레포 클론뿐이며,
  star·fork·issue·PR·댓글 같은 쓰기 호출은 코드에 존재하지 않는다.
- 레포당 depth 1 · blob 없는 스파스 클론 1회로 스킬 폴더만 받고, 요청 사이에 간격을 둔다.
- 내려받은 코드는 **실행하지 않는다**. 문법 검사는 파싱만 한다(단위 테스트로 보장).
- `LLM_API_KEY` 를 설정한 경우에만 요약할 레포의 README 앞부분과 스킬 이름·설명(모두 공개 내용)을
  LLM API 로 보낸다. 받은 글은 형식·길이·스킬 이름을 검사하고 ‘AI 요약’ 으로 구분해 표시한다.
- 서브모듈·LFS·심볼릭 링크는 받지 않고, 토큰은 git·검사 프로세스에 전달하지 않는다.
- 클론은 검사 직후 삭제하고, 리포트에는 메타데이터와 검사 결과만 싣는다.
- 워크플로 권한은 이 레포에 대한 `contents: write` 하나뿐이다.

## 홈서버에서 실행 (Docker)

Docker 와 Docker Compose 만 있으면 됩니다. 컨테이너가 상주하며 매일 정해진 시각에 실행합니다.

```bash
git clone https://github.com/lvalue-dev/skills-tracker.git && cd skills-tracker
cp .env.example .env            # PUID/PGID(`id -u`, `id -g`), 실행 시각, (선택) 토큰
mkdir -p output && cp -r data reports output/   # 지난 데이터로 시작해야 첫날부터 전일 대비가 나온다
docker compose up -d --build    # 이후 매일 RUN_AT(기본 08:23 KST)에 자동 실행
docker compose logs -f tracker  # 진행 상황 확인
```

- **웹에서 보기**: `http://<홈서버 주소>:8080` — 날짜별 목록에서 웹 리포트나 PDF 를 엽니다 (휴대폰 화면 대응).
  포트는 `.env` 의 `WEB_PORT`. 인증이 없으니 집 안 네트워크에서만 쓰고 공유기 포트포워딩으로 열지 마세요.
- 리포트는 홈서버의 `output/reports/YYYY-MM-DD.pdf`(웹은 `.html`), 원본 데이터는 `output/data/` 에 쌓입니다.
  git 이 관리하지 않는 폴더라서 `git pull` 과 부딪히지 않습니다 (`.env` 의 `OUTPUT_DIR` 로 위치 변경).
- 서버가 꺼져 있다가 실행 시각을 지나 켜지면 오늘자 리포트가 없을 때 바로 한 번 실행합니다.
- 실패하면 30분 간격으로 하루 최대 3번까지만 다시 시도합니다.
- 지금 바로 한 번 실행: `docker compose run --rm tracker scripts/run_daily.sh`
- 코드 갱신: `git pull && docker compose up -d --build` — 컨테이너는 빌드 시점의 코드로 돌기 때문에
  `git pull` 만 해서는 반영되지 않고 반드시 다시 빌드해야 합니다.
- 이미 만든 날짜의 리포트를 새 코드 형식으로 다시 만들기 (네트워크 접근 없음):
  `docker compose run --rm tracker python -m tracker --render-only --date YYYY-MM-DD`

**이전 구성(레포의 `data/`·`reports/` 에 직접 쓰던 방식)에서 옮기기** — `git pull` 이 충돌로 실패한다면:

```bash
docker compose down
mkdir -p output && mv data reports output/   # 홈서버가 만든 결과물을 통째로 옮긴다 (삭제 없음)
git checkout -- data reports                 # git 이 관리하는 원래 파일을 되살린다
git pull
docker compose up -d --build
```
- GitHub 토큰은 없어도 됩니다. 넣는다면 권한을 하나도 주지 않은 토큰이면 충분하고, 검색 조회에만 쓰입니다.
- (선택) `.env` 에 `LLM_API_KEY` 를 넣으면 `tracker/notes_ko.json` 에 한글 요약이 없는 레포를 LLM 이 요약해
  ‘AI 요약’ 으로 표시합니다 (OpenAI 호환 API, 기본 NVIDIA NIM). 공개 레포의 README 앞부분과 스킬 이름·설명만
  보내고, 결과는 `data/.cache/notes_auto.json` 에 보관해 스킬 구성이 바뀌거나 30일이 지나야 다시 부릅니다.
- 컨테이너는 비루트 사용자로, 모든 권한(capability)을 제거한 채 실행됩니다. 이미지 크기는 약 1.6GB 입니다.
- 홈서버는 리포트를 로컬(`output/`)에만 저장하고 GitHub 로 푸시하지 않습니다.

홈서버로 옮긴 뒤에는 중복 수집을 피하도록 GitHub Actions 의 **Daily skills report** 워크플로를
비활성화하세요 (Actions 탭 → 워크플로 선택 → Disable workflow).

## GitHub Actions 로 실행

`.github/workflows/daily-report.yml` 이 매일 08:23 KST 에 실행되어 결과를 이 레포에 커밋합니다.
Actions 탭에서 **Run workflow** 로 수동 실행할 수도 있습니다.

## 직접 실행 (Docker 없이)

```bash
scripts/run_daily.sh                   # 폰트·의존성 확인 → 자체 테스트 → 오늘자 수집·테스트·리포트
scripts/run_daily.sh --render-only     # 네트워크 없이 저장된 data/<오늘>.json 으로 리포트만 재생성
python -m unittest discover -s tests   # 검사기 자체 테스트만
```

필요한 것: Python 3.11+, `git`, `node`, Chrome/Chromium(`CHROME_BIN` 으로 지정 가능),
한글 폰트(`fonts-noto-cjk`). `gh` 는 있으면 쓰고 없으면 HTTPS 로 직접 조회합니다.

## 설정

`config.json` 에서 검색어(`search_queries`), 수집 개수(`top_n`), 시간대, 실행 시각(`run_at`), 요청 간격, 상한을 바꿀 수 있습니다.
홈서버에서는 `config.json` 을 고친 뒤 `docker compose up -d --build` 로 반영합니다.
