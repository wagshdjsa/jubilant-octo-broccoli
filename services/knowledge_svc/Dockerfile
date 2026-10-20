FROM docker.m.daocloud.io/library/python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
COPY packages/ ./packages/
COPY services/knowledge_svc/ ./services/knowledge_svc/

ENV PYTHONPATH=/app
RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/ && \
    pip install --no-cache-dir -r requirements.txt

EXPOSE 8003
CMD ["python", "-m", "uvicorn", "services.knowledge_svc.main:app", "--host", "0.0.0.0", "--port", "8003"]