FROM docker.m.daocloud.io/library/python:3.11-slim

WORKDIR /app

COPY requirements.txt ./
COPY packages/ ./packages/
COPY services/tool_svc/ ./services/tool_svc/

ENV PYTHONPATH=/app
RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/ && \
    pip install --no-cache-dir -r requirements.txt

EXPOSE 8004
CMD ["python", "-m", "uvicorn", "services.tool_svc.main:app", "--host", "0.0.0.0", "--port", "8004"]