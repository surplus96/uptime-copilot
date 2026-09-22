FROM python:3.12-slim

WORKDIR /app

# 임베딩 모델(intfloat/multilingual-e5-small, 약 476MB) 캐시 위치를 명시적으로 고정한다.
# 기본값은 ~/.cache/huggingface = 이미지 레이어 안이라 --build 때마다 통째로 날아가고,
# 매번 처음부터 다시 받느라 기동이 2~4분씩 걸렸다(2026-09-22 build-doctor 실측:
# cold 118초 vs warm 6.4초). docker-compose.yml에서 이 경로에 named volume을 물린다.
ENV HF_HOME=/opt/hf_cache

RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
