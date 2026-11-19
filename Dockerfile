# API server for a stable demo URL (Railway, Render, Fly or a small VM). The web app runs separately.
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server ./server
COPY data/seed.json data/sample_policy_wording.md ./data/
ENV PORT=8000
CMD ["sh", "-c", "uvicorn server.main:app --host 0.0.0.0 --port ${PORT}"]
