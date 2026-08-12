# 1. Builder Stage
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy AS builder

WORKDIR /code

COPY requirements.txt .
# 메모리 부족(OOM) 현상을 방지하기 위해, 훨씬 적은 메모리를 사용하는 uv 패키지 매니저를 활용합니다.
RUN pip install uv && uv pip install --user --no-cache -r requirements.txt

# 2. Runner Stage
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy AS runner

WORKDIR /code

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /root/.local /root/.local
COPY . .

ENV PATH=/root/.local/bin:$PATH
ENV PYTHONPATH=/code

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]