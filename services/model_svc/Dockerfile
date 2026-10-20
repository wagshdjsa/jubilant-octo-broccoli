FROM docker.m.daocloud.io/library/python:3.11-slim

WORKDIR /app

# pgvector/psycopg2 需要编译依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
COPY packages/ ./packages/
COPY services/model_svc/ ./services/model_svc/

ENV PYTHONPATH=/app
RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/ && \
    pip install --no-cache-dir -r requirements.txt

EXPOSE 8005
CMD ["python", "-m", "uvicorn", "services.model_svc.main:app", "--host", "0.0.0.0", "--port", "8005"]