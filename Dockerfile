# 1. Builder Stage
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy AS builder

WORKDIR /code

COPY requirements.txt constraints.txt ./
# 메모리 부족(OOM) 현상을 방지하기 위해, 훨씬 적은 메모리를 사용하는 uv 패키지 매니저를 활용합니다.
# uv는 --user 대신 가상환경(venv) 사용을 강제하므로, /opt/venv에 가상환경을 만들어 설치합니다.
RUN pip install uv && uv venv /opt/venv && VIRTUAL_ENV=/opt/venv uv pip install --no-cache -r requirements.txt -c constraints.txt

# 2. Runner Stage
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy AS runner

WORKDIR /code

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv
COPY . .

ENV PATH=/opt/venv/bin:$PATH
ENV PYTHONPATH=/code

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]