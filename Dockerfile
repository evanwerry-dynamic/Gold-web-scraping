FROM python:3.11-slim

WORKDIR /app

# System deps for web3/cryptography
RUN apt-get update -qq && apt-get install -y --no-install-recommends \
    gcc g++ libssl-dev && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p data/polymarket polymarket/state

ENV PAPER_TRADING=true
ENV DASHBOARD_PORT=8000

EXPOSE 8000

CMD ["sh", "-c", "uvicorn polymarket.dashboard.backend.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
