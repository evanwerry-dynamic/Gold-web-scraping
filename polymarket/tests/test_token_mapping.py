"""
Tests for Gamma API outcome→token mapping in chainlink_rtds.py.

Root-cause regression test for the systematic 100% loss bug: the bot
previously assumed clobTokenIds[0] is always the YES/Up token. If Gamma
ever orders outcomes as ["Down", "Up"] for a given market, that assumption
silently buys the token that pays off OPPOSITE the directional signal,
while the bot's own bookkeeping still calls it a "win" or "loss" using the
same flipped index — so nothing looks wrong in the logs even though every
trade is buying the wrong side. map_yes_no_tokens / _yes_outcome_index must
resolve the correct token by reading the outcome label text, not position.
"""
import json

from polymarket.feeds.chainlink_rtds import map_yes_no_tokens, _yes_outcome_index


def _market(outcomes, token_ids):
    return {
        "clobTokenIds": json.dumps(token_ids),
        "outcomes": json.dumps(outcomes),
    }


class TestMapYesNoTokens:

    def test_standard_order_yes_first(self):
        m = _market(["Up", "Down"], ["111", "222"])
        yes_id, no_id = map_yes_no_tokens(m)
        assert yes_id == "111"
        assert no_id == "222"

    def test_reversed_order_yes_second(self):
        m = _market(["Down", "Up"], ["111", "222"])
        yes_id, no_id = map_yes_no_tokens(m)
        assert yes_id == "222"
        assert no_id == "111"

    def test_yes_no_labels_instead_of_up_down(self):
        m = _market(["No", "Yes"], ["aaa", "bbb"])
        yes_id, no_id = map_yes_no_tokens(m)
        assert yes_id == "bbb"
        assert no_id == "aaa"

    def test_missing_outcomes_field_falls_back_to_index_order(self):
        m = {"clobTokenIds": json.dumps(["111", "222"])}
        yes_id, no_id = map_yes_no_tokens(m)
        assert yes_id == "111"
        assert no_id == "222"

    def test_malformed_token_ids_returns_none(self):
        m = {"clobTokenIds": json.dumps(["111"])}
        assert map_yes_no_tokens(m) is None


class TestYesOutcomeIndex:

    def test_standard_order(self):
        m = _market(["Up", "Down"], ["111", "222"])
        assert _yes_outcome_index(m) == 0

    def test_reversed_order(self):
        m = _market(["Down", "Up"], ["111", "222"])
        assert _yes_outcome_index(m) == 1

    def test_missing_outcomes_defaults_to_zero(self):
        assert _yes_outcome_index({}) == 0


class TestOutcomePriceConsistency:
    """The yes_token_id returned by map_yes_no_tokens must correspond to the
    same index used to read the YES price out of outcomePrices — otherwise
    resolution and execution could disagree on which token is "YES" even
    after the label-based fix."""

    def test_yes_token_and_yes_price_index_agree_when_reversed(self):
        m = _market(["Down", "Up"], ["111", "222"])
        m["outcomePrices"] = json.dumps(["0", "1"])  # Down lost, Up won
        yes_id, _ = map_yes_no_tokens(m)
        yes_idx = _yes_outcome_index(m)
        prices = json.loads(m["outcomePrices"])
        assert yes_id == "222"
        assert float(prices[yes_idx]) == 1.0  # Up token correctly reads as the winner
