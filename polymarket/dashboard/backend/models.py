"""Pydantic models for dashboard WebSocket event envelope."""
from typing import Any, Literal
from pydantic import BaseModel


class TradeEvent(BaseModel):
    market_id: str
    strategy: str
    side: str
    entry_price: float
    fair_value: float
    edge: float
    dollar_size: float
    pnl: float | None = None
    paper: bool = True


class PositionEvent(BaseModel):
    market_id: str
    side: str
    shares: float
    cost_basis: float
    current_value: float
    unrealized_pnl: float


class PnLEvent(BaseModel):
    total: float
    today: float
    peak: float
    drawdown_pct: float


class BookEvent(BaseModel):
    market_id: str
    bids: list[dict]
    asks: list[dict]
    mid: float


class HealthEvent(BaseModel):
    ws_binance: bool
    ws_clob: bool
    matic_balance: float
    pusd_balance: float
    open_positions: int
    strategy_phase: str


class StrategyEvent(BaseModel):
    phase: str  # SCAN | FAIR | EDGE | LIMIT | FILL | HOLD


class DashboardMessage(BaseModel):
    type: Literal["trade", "position", "pnl", "book", "health", "strategy"]
    data: Any
