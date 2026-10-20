FROM docker.m.daocloud.io/library/python:3.11-slim

WORKDIR /app

COPY requirements.txt ./
COPY packages/ ./packages/
COPY services/api_gateway/ ./services/api_gateway/

ENV PYTHONPATH=/app
RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/ && \
    pip install --no-cache-dir fastapi "uvicorn[standard]" httpx redis pydantic pydantic-settings

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "services.api_gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]