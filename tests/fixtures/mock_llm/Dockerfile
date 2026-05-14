# Copyright 2026 Firefly Software Solutions Inc
FROM python:3.13-slim

WORKDIR /app
RUN pip install --no-cache-dir fastapi==0.115.* "uvicorn[standard]==0.32.*"

COPY server.py /app/server.py
COPY canned /canned

ENV MOCK_LLM_CANNED_DIR=/canned
EXPOSE 8500
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8500", "--log-level", "warning"]
