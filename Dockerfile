# 홈서버용 이미지: 수집·정적 테스트·PDF 생성에 필요한 것만 담는다.
FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CHROME_BIN=/usr/bin/chromium \
    HOME=/tmp

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      git nodejs chromium fonts-noto-cjk fontconfig tzdata ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY config.json ./
COPY tracker ./tracker
COPY tests ./tests
COPY scripts ./scripts

# data/ 와 reports/ 는 호스트 폴더를 마운트한다 (compose.yaml 참고).
CMD ["python", "-m", "tracker.schedule"]
