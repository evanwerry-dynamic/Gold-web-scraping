"""Tests for position resolution — win/loss accounting, forced resolution."""
import pytest
import time
from polymarket.oracle_buffer import OracleBuffer, ActiveMarket, OpenPosition
from polymarket.feeds.chainlink_rtds import _resolve_previous_window, _synthetic_paper_market


def _market(open_price, btc_price, secs_left=5):
    now = time.time()
    return ActiveMarket(
        market_id="res-test",
        condition_id="cond-res",
        yes_token_id="tok-yes",
        no_token_id="tok-no",
        window_open_ts=now - (300 - secs_left),
        window_end_ts=now + secs_left,
        window_open_price=open_price,
    ), btc_price


def _add_pos(oracle, order_id, side, shares=10.0, cost_basis=8.5):
    pos = OpenPosition(
        market_id="res-test",
        condition_id="cond-res",
        token_id="tok-yes",
        side=side,
        shares=shares,
        cost_basis=cost_basis,
    )
    oracle.open_positions[order_id] = pos
    return pos


class TestResolveWindow:
    def test_up_position_wins_when_price_rose(self):
        oracle = OracleBuffer(bankroll=490.0, paper_trading=True)
        market, btc = _market(open_price=50000, btc_price=50100)
        oracle.active_market = market
        oracle.btc_price = btc
        _add_pos(oracle, "ord-up", "UP")

        _resolve_previous_window(oracle)

        pos = oracle.open_positions["ord-up"]
        assert pos.resolved is True
        assert pos.resolution == 1.0  # won

    def test_up_position_loses_when_price_fell(self):
        oracle = OracleBuffer(bankroll=490.0, paper_trading=True)
        market, btc = _market(open_price=50000, btc_price=49900)
        oracle.active_market = market
        oracle.btc_price = btc
        _add_pos(oracle, "ord-up-loss", "UP")

        _resolve_previous_window(oracle)

        pos = oracle.open_positions["ord-up-loss"]
        assert pos.resolved is True
        assert pos.resolution == 0.0  # lost

    def test_no_position_wins_when_price_fell(self):
        oracle = OracleBuffer(bankroll=490.0, paper_trading=True)
        market, btc = _market(open_price=50000, btc_price=49900)
        oracle.active_market = market
        oracle.btc_price = btc
        _add_pos(oracle, "ord-no-win", "NO")

        _resolve_previous_window(oracle)

        pos = oracle.open_positions["ord-no-win"]
        assert pos.resolution == 1.0  # NO wins when price fell

    def test_no_position_loses_when_price_rose(self):
        oracle = OracleBuffer(bankroll=490.0, paper_trading=True)
        market, btc = _market(open_price=50000, btc_price=50100)
        oracle.active_market = market
        oracle.btc_price = btc
        _add_pos(oracle, "ord-no-loss", "NO")

        _resolve_previous_window(oracle)

        pos = oracle.open_positions["ord-no-loss"]
        assert pos.resolution == 0.0  # NO loses when price rose

    def test_tie_price_goes_to_up(self):
        """When final == open exactly, UP wins (ties go to UP by convention)."""
        oracle = OracleBuffer(bankroll=490.0, paper_trading=True)
        market, btc = _market(open_price=50000, btc_price=50000)
        oracle.active_market = market
        oracle.btc_price = btc
        _add_pos(oracle, "ord-tie-up", "UP")

        _resolve_previous_window(oracle)

        pos = oracle.open_positions["ord-tie-up"]
        assert pos.resolution == 1.0

    def test_already_resolved_skipped(self):
        oracle = OracleBuffer(bankroll=490.0, paper_trading=True)
        market, btc = _market(open_price=50000, btc_price=50100)
        oracle.active_market = market
        oracle.btc_price = btc
        pos = _add_pos(oracle, "ord-already", "UP")
        pos.resolved = True
        pos.resolution = 0.0  # explicitly set to lost

        _resolve_previous_window(oracle)

        # Should NOT be overwritten
        assert pos.resolution == 0.0


class TestSyntheticPaperMarket:
    def test_market_id_contains_timestamp(self):
        m = _synthetic_paper_market(50000)
        assert m.market_id.startswith("paper-btc-5m-")

    def test_window_end_300s_after_open(self):
        m = _synthetic_paper_market(50000)
        assert m.window_end_ts - m.window_open_ts == pytest.approx(300, abs=1)

    def test_open_price_captured(self):
        m = _synthetic_paper_market(51234.56)
        assert m.window_open_price == pytest.approx(51234.56)
