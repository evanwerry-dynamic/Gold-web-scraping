"""
Risk manager and position sizer for Mad Scientist.

Quarter-Kelly sizing with hard caps.
Circuit breakers: daily loss, peak drawdown, total loss, correlation, loss velocity.
"""
import time
import logging
from collections import deque

log = logging.getLogger(__name__)


class RiskManager:
    def __init__(
        self,
        bankroll: float,
        max_correlated_pct: float = 0.20,
        daily_loss_limit: float = 0.05,
        drawdown_limit: float = 0.25,
        monthly_loss_limit: float = 0.15,
        total_loss_limit: float = 0.40,
    ):
        self.initial = bankroll
        self.peak = bankroll
        self.daily_start = bankroll
        self.monthly_start = bankroll
        self.max_correlated_pct = max_correlated_pct
        self.daily_loss_limit = daily_loss_limit
        self.drawdown_limit = drawdown_limit
        self.monthly_loss_limit = monthly_loss_limit
        self.total_loss_limit = total_loss_limit

        self.consecutive_losses: int = 0
        self.consecutive_wins: int = 0
        self._loss_times: deque = deque(maxlen=20)

    def allow_trade(
        self,
        current_bankroll: float,
        correlated_exposure: float = 0.0,
    ) -> tuple[bool, str]:
        """
        Returns (allowed, reason). Reason is empty string when allowed.

        correlated_exposure: total notional currently at risk in BTC-correlated positions.
        """
        if current_bankroll <= 0:
            return False, "Bankroll is zero or negative"
        if self.initial <= 0 or self.daily_start <= 0 or self.monthly_start <= 0:
            return False, "RiskManager not properly initialised (zero bankroll)"

        # Permanent halt
        total_loss_pct = (self.initial - current_bankroll) / self.initial
        if total_loss_pct > self.total_loss_limit:
            return False, f"40% total loss ({total_loss_pct:.1%}) — PERMANENT HALT"

        # Peak drawdown
        drawdown = (self.peak - current_bankroll) / self.peak
        if drawdown > self.drawdown_limit:
            return False, f"25% drawdown ({drawdown:.1%}) — paused"

        # Daily loss
        daily_loss = (self.daily_start - current_bankroll) / self.daily_start
        if daily_loss > self.daily_loss_limit:
            return False, f"5% daily loss ({daily_loss:.1%}) — paused until tomorrow"

        # Monthly loss
        monthly_loss = (self.monthly_start - current_bankroll) / self.monthly_start
        if monthly_loss > self.monthly_loss_limit:
            return False, f"15% monthly loss ({monthly_loss:.1%}) — manual restart required"

        # Correlation cap
        if correlated_exposure > 0 and current_bankroll > 0:
            corr_pct = correlated_exposure / current_bankroll
            if corr_pct > self.max_correlated_pct:
                return False, f"BTC correlation cap ({corr_pct:.1%} > {self.max_correlated_pct:.0%})"

        # Loss velocity: 5+ losses in 30 minutes
        now = time.time()
        recent = sum(1 for t in self._loss_times if now - t < 1800)
        if recent >= 5:
            return False, f"Loss velocity: {recent} losses in 30min — cooling off"

        self.peak = max(self.peak, current_bankroll)
        return True, ""

    def on_trade_result(self, pnl: float) -> None:
        """Call after each resolved trade."""
        if pnl < 0:
            self.consecutive_losses += 1
            self.consecutive_wins = 0
            self._loss_times.append(time.time())
        else:
            self.consecutive_wins += 1
            self.consecutive_losses = 0

    def position_scale_factor(self) -> float:
        """Reduce size after loss streaks, cap growth after wins."""
        factor = 1.0 - 0.20 * min(self.consecutive_losses, 4)
        return max(0.25, min(1.5, factor))

    def reset_daily(self, current_bankroll: float) -> None:
        self.daily_start = current_bankroll

    def reset_monthly(self, current_bankroll: float) -> None:
        self.monthly_start = current_bankroll
        self.daily_start = current_bankroll  # new month = new day


def kelly_size(
    fair_prob: float,
    market_price: float,
    bankroll: float,
    fraction: float = 0.25,
    max_pct: float = 0.015,
    scale_factor: float = 1.0,
) -> dict:
    """
    Quarter-Kelly position sizing with hard cap.

    fraction=0.25 delivers ~50% of full-Kelly growth rate with ~75% less variance.
    max_pct=0.015 hard-caps each trade at 1.5% of bankroll (~$25 at $1700 bankroll).
    At $0.85 entry a full loss is the entire position cost, so smaller cap reduces
    the win/loss asymmetry from ~6:1 to ~6:1 at half the magnitude.

    Returns dict with dollar_size, shares, kelly_pct, edge.
    Returns zero-sized dict when no edge exists.
    """
    if market_price <= 0 or market_price >= 1:
        return {"dollar_size": 0.0, "shares": 0.0, "kelly_pct": 0.0, "edge": 0.0}
    if fair_prob <= market_price:
        return {"dollar_size": 0.0, "shares": 0.0, "kelly_pct": 0.0, "edge": 0.0}
    if bankroll <= 0:
        return {"dollar_size": 0.0, "shares": 0.0, "kelly_pct": 0.0, "edge": 0.0}

    b = (1.0 - market_price) / market_price  # decimal odds
    q = 1.0 - fair_prob
    full_kelly = max(0.0, (b * fair_prob - q) / b)

    applied = min(full_kelly * fraction * scale_factor, max_pct)
    dollar_size = bankroll * applied
    shares = dollar_size / market_price if market_price > 0 else 0.0

    return {
        "dollar_size": round(dollar_size, 2),
        "shares": round(shares, 4),
        "kelly_pct": round(applied, 6),
        "edge": round(fair_prob - market_price, 4),
    }
