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

# System deps for web3/cryptography + Node.js for frontend build
RUN apt-get update -qq && apt-get install -y --no-install-recommends \
    gcc g++ libssl-dev curl && rm -rf /var/lib/apt/lists/* \
 && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
 && apt-get install -y nodejs && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && python -m uvicorn --version

COPY . .
# Overlay the freshly-built frontend static export
COPY --from=frontend /frontend/out polymarket/dashboard/frontend/out

RUN mkdir -p data/polymarket polymarket/state

# Build Next.js static export so the dashboard is served at /
RUN cd polymarket/dashboard/frontend && npm ci --prefer-offline 2>/dev/null || npm install && npm run build

ENV PAPER_TRADING=true

EXPOSE 8000

CMD ["sh", "-c", "python -m uvicorn polymarket.dashboard.backend.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
