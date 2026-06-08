# Stage 1: Build Next.js static export
FROM node:20-slim AS frontend
WORKDIR /frontend
COPY polymarket/dashboard/frontend/package*.json ./
RUN npm ci
COPY polymarket/dashboard/frontend/ ./
RUN npm run build

# Stage 2: Python + FastAPI runtime
FROM python:3.11-slim
WORKDIR /app

RUN apt-get update -qq && apt-get install -y --no-install-recommends \
    gcc g++ libssl-dev && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && python -m uvicorn --version

COPY . .
# Overlay the freshly-built frontend static export
COPY --from=frontend /frontend/out polymarket/dashboard/frontend/out

RUN mkdir -p data/polymarket polymarket/state

ENV PAPER_TRADING=true

EXPOSE 8000

CMD ["sh", "-c", "python -m uvicorn polymarket.dashboard.backend.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
