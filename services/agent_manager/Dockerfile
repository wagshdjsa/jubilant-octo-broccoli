FROM docker.m.daocloud.io/library/python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
COPY packages/ ./packages/
COPY services/agent_manager/ ./services/agent_manager/

ENV PYTHONPATH=/app
RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/ && \
    pip install --no-cache-dir -r requirements.txt

EXPOSE 8001
CMD ["python", "-m", "uvicorn", "services.agent_manager.main:app", "--host", "0.0.0.0", "--port", "8001"]