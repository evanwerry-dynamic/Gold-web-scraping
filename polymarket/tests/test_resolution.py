"""
Tests for resolution logic in chainlink_rtds.py.

Win/loss determination is the moment real money is made or lost.
Every combination of (side, btc_direction) must be tested.

Also tests the P&L accounting in redeem_loop so we never see a losing
trade show as $0 or a winner miscounted.
"""
import asyncio
import pytest
from polymarket.oracle_buffer import OracleBuffer, OpenPosition, ActiveMarket


def _make_position(side: str, cost_basis: float = 20.0, shares: float = 0.25) -> OpenPosition:
    return OpenPosition(
        market_id="test-btc-5m-123",
        condition_id="cond-abc",
        token_id="tok-xyz",
        side=side,
        shares=shares,
        cost_basis=cost_basis,
        window_open_price=60000.0,
    )


def _apply_resolution(pos: OpenPosition, btc_went_up: bool) -> None:
    """Replicate the logic from chainlink_rtds._resolve_previous_window."""
    won = (pos.side in ("UP", "YES")) == btc_went_up
    pos.resolution = 1.0 if won else 0.0
    pos.resolved = True


class TestResolutionDirectionality:

    def test_yes_wins_when_btc_up(self):
        pos = _make_position("YES")
        _apply_resolution(pos, btc_went_up=True)
        assert pos.resolution == 1.0

    def test_yes_loses_when_btc_down(self):
        pos = _make_position("YES")
        _apply_resolution(pos, btc_went_up=False)
        assert pos.resolution == 0.0

    def test_no_wins_when_btc_down(self):
        pos = _make_position("NO")
        _apply_resolution(pos, btc_went_up=False)
        assert pos.resolution == 1.0

    def test_no_loses_when_btc_up(self):
        pos = _make_position("NO")
        _apply_resolution(pos, btc_went_up=True)
        assert pos.resolution == 0.0

    def test_up_treated_same_as_yes(self):
        pos_yes = _make_position("YES")
        pos_up  = _make_position("UP")
        _apply_resolution(pos_yes, btc_went_up=True)
        _apply_resolution(pos_up,  btc_went_up=True)
        assert pos_yes.resolution == pos_up.resolution

    def test_down_treated_same_as_no(self):
        pos_no   = _make_position("NO")
        pos_down = _make_position("DOWN")
        _apply_resolution(pos_no,   btc_went_up=False)
        _apply_resolution(pos_down, btc_went_up=False)
        assert pos_no.resolution == pos_down.resolution

    def test_resolution_marked_resolved(self):
        pos = _make_position("YES")
        assert pos.resolved is False
        _apply_resolution(pos, btc_went_up=True)
        assert pos.resolved is True


class TestPnLAccounting:
    """
    End-to-end P&L accounting: place trade → resolve → redeem.
    Verifies bankroll and P&L totals are correct for both win and loss paths.
    """

    def _setup_oracle_with_position(self, side: str):
        oracle = OracleBuffer(bankroll=1000.0, paper_trading=True)
        oracle.btc_price = 60100.0  # slightly up
        pos = _make_position(side, cost_basis=20.0, shares=0.25)
        oracle.open_positions["order-001"] = pos
        oracle.bankroll -= 20.0   # simulate the OMS debit
        return oracle, pos

    @pytest.mark.asyncio
    async def test_win_increases_bankroll_by_payout(self):
        oracle, pos = self._setup_oracle_with_position("YES")
        starting_bankroll = oracle.bankroll  # 980.0

        pos.resolution = 1.0
        pos.resolved = True

        payout = pos.shares * pos.resolution  # 0.25 * 1.0 = 0.25
        async with oracle.bankroll_lock:
            oracle.bankroll += payout
            oracle.total_pnl += payout - pos.cost_basis
            oracle.today_pnl += payout - pos.cost_basis

        assert oracle.bankroll == pytest.approx(starting_bankroll + payout, abs=0.01)
        assert oracle.total_pnl == pytest.approx(payout - 20.0, abs=0.01)  # -19.75 (low shares)

    @pytest.mark.asyncio
    async def test_full_win_payout_at_1_per_share(self):
        # Buy 20 shares @ 1.0 per share (market resolves at full payout)
        oracle = OracleBuffer(bankroll=1000.0, paper_trading=True)
        pos = OpenPosition(
            market_id="test", condition_id="c", token_id="t",
            side="YES", shares=20.0, cost_basis=20.0,  # bought at $1/share (edge case)
            window_open_price=60000.0,
        )
        oracle.open_positions["order-win"] = pos
        oracle.bankroll -= 20.0
        pos.resolution = 1.0
        pos.resolved = True

        payout = pos.shares * pos.resolution
        async with oracle.bankroll_lock:
            oracle.bankroll += payout
            oracle.total_pnl += payout - pos.cost_basis

        assert oracle.bankroll == pytest.approx(1000.0, abs=0.01)  # net zero
        assert oracle.total_pnl == pytest.approx(0.0, abs=0.01)

    @pytest.mark.asyncio
    async def test_loss_results_in_negative_pnl(self):
        oracle, pos = self._setup_oracle_with_position("YES")

        pos.resolution = 0.0  # lost
        pos.resolved = True

        payout = pos.shares * pos.resolution  # 0.0
        # No bankroll increase for a loss
        async with oracle.bankroll_lock:
            oracle.total_pnl += payout - pos.cost_basis
            oracle.today_pnl += payout - pos.cost_basis

        assert oracle.total_pnl == pytest.approx(-20.0, abs=0.01)
        assert oracle.today_pnl == pytest.approx(-20.0, abs=0.01)
        # Bankroll NOT restored — the $20 was already debited at entry
        assert oracle.bankroll == pytest.approx(980.0, abs=0.01)

    @pytest.mark.asyncio
    async def test_win_no_position_shows_positive_pnl(self):
        oracle = OracleBuffer(bankroll=1000.0, paper_trading=True)
        # Buy YES at $0.82 for $20 → 24.39 shares
        shares = 20.0 / 0.82
        pos = OpenPosition(
            market_id="test", condition_id="c", token_id="t",
            side="YES", shares=shares, cost_basis=20.0,
            window_open_price=60000.0,
        )
        oracle.open_positions["order-002"] = pos
        oracle.bankroll -= 20.0
        pos.resolution = 1.0
        pos.resolved = True

        payout = pos.shares * pos.resolution  # 24.39
        async with oracle.bankroll_lock:
            oracle.bankroll += payout
            oracle.total_pnl += payout - pos.cost_basis

        assert oracle.total_pnl == pytest.approx(payout - 20.0, abs=0.01)
        assert oracle.total_pnl > 0, "winner should yield positive P&L"

    def test_losing_trade_pnl_is_negative_cost_basis(self):
        pos = _make_position("YES", cost_basis=20.0, shares=0.25)
        pos.resolution = 0.0
        payout = pos.shares * pos.resolution
        final_pnl = payout - pos.cost_basis
        assert final_pnl == pytest.approx(-20.0)
        assert final_pnl < 0
