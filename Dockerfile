FROM node:22-alpine AS frontend

WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY content ./content
COPY --from=frontend /web/dist ./frontend/dist

ENV PYTHONUNBUFFERED=1 \
    CHECKPOINT_DB=/data/checkpoints.db \
    REDIS_URL=redis://redis:6379/0 \
    FRONTEND_DIST=/app/frontend/dist

RUN mkdir -p /data

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
