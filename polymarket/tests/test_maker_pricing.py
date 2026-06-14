"""
Tests for the Strategy B fair-value maker pricing core (compute_maker_quotes).

These verify the adverse-selection-hardened behaviour:
- quotes are anchored to fair value, never above it (no overpaying)
- both sides rest strictly passive (below the book ask)
- inventory skew pushes quotes toward flat, not toward a directional bet
- spread widens with volatility
- a side is dropped when the book is richer than fair (would overpay)
"""
import math
import pytest

from polymarket.strategies.maker_loop import compute_maker_quotes, _floor_to_tick


def _base(**overrides):
    """A flat, calm baseline call; override individual fields per test."""
    args = dict(
        fair=0.50,
        yes_bid=0.49,
        yes_ask=0.51,
        no_ask=0.51,
        sigma_per_second=0.0002,
        secs_left=120.0,
        bankroll=1000.0,
        inventory_up_value=0.0,
        quote_size=50.0,
    )
    args.update(overrides)
    return compute_maker_quotes(**args)


def test_floor_to_tick_rounds_down():
    assert _floor_to_tick(0.489) == 0.48
    assert _floor_to_tick(0.4799999) == 0.47
    assert _floor_to_tick(0.50) == 0.50


def test_never_bids_above_fair():
    """A buy price must always sit below that side's fair value (+EV by construction)."""
    r = _base(fair=0.50)
    if r["yes"]:
        assert r["yes"]["price"] < 0.50
    if r["no"]:
        # NO fair = 1 - 0.50 = 0.50
        assert r["no"]["price"] < 0.50


def test_quotes_are_passive():
    """Quotes must rest strictly below the opposing book ask (POST_ONLY safe)."""
    r = _base(yes_ask=0.51, no_ask=0.55)
    if r["yes"]:
        assert r["yes"]["price"] <= round(0.51 - 0.01, 2)
    if r["no"]:
        assert r["no"]["price"] <= round(0.55 - 0.01, 2)


def test_symmetric_when_flat_and_calm():
    """Flat inventory + symmetric book => symmetric YES/NO prices around 0.50."""
    r = _base(fair=0.50, inventory_up_value=0.0, yes_ask=0.99, no_ask=0.99)
    assert r["yes"] is not None and r["no"] is not None
    assert r["yes"]["price"] == pytest.approx(r["no"]["price"], abs=0.01)
    assert r["skew"] == 0.0


def test_long_up_inventory_skews_toward_no():
    """Net long UP => bid LESS for YES and MORE for NO (push back to flat)."""
    flat = _base(inventory_up_value=0.0, yes_ask=0.99, no_ask=0.99)
    long_up = _base(inventory_up_value=500.0, yes_ask=0.99, no_ask=0.99)
    assert long_up["skew"] > 0
    # YES bid drops (we want less YES); NO bid rises (we want more NO).
    assert long_up["yes"]["price"] <= flat["yes"]["price"]
    assert long_up["no"]["price"] >= flat["no"]["price"]


def test_short_up_inventory_skews_toward_yes():
    """Net short UP (long NO) => bid MORE for YES, LESS for NO."""
    flat = _base(inventory_up_value=0.0, yes_ask=0.99, no_ask=0.99)
    long_no = _base(inventory_up_value=-500.0, yes_ask=0.99, no_ask=0.99)
    assert long_no["skew"] < 0
    assert long_no["yes"]["price"] >= flat["yes"]["price"]
    assert long_no["no"]["price"] <= flat["no"]["price"]


def test_higher_vol_widens_spread():
    calm = _base(sigma_per_second=0.0001)
    wild = _base(sigma_per_second=0.005)
    assert wild["half_spread"] > calm["half_spread"]


def test_half_spread_capped():
    r = _base(sigma_per_second=1.0, secs_left=300.0)
    assert r["half_spread"] <= 0.10


def test_capture_gate_drops_both_sides_when_min_capture_exceeds_spread():
    """If the required edge (min_capture) is larger than the half-spread the model
    can offer, neither side is worth posting and both are dropped."""
    r = compute_maker_quotes(
        fair=0.50, yes_bid=0.49, yes_ask=0.99, no_ask=0.99,
        sigma_per_second=0.0002, secs_left=120.0, bankroll=1000.0,
        inventory_up_value=0.0, quote_size=50.0, min_capture=0.20,
    )
    assert r["yes"] is None
    assert r["no"] is None


def test_passivity_clamp_prevents_crossing_thin_book():
    """A very low book ask must force our bid below it, never above (no crossing)."""
    r = _base(fair=0.50, yes_ask=0.20, no_ask=0.99)
    if r["yes"]:
        assert r["yes"]["price"] <= round(0.20 - 0.01, 2)


def test_dollar_size_passthrough():
    r = _base(quote_size=42.0, yes_ask=0.99, no_ask=0.99)
    if r["yes"]:
        assert r["yes"]["dollar_size"] == 42.0
    if r["no"]:
        assert r["no"]["dollar_size"] == 42.0
