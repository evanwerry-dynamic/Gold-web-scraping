"""
Signature contract for _redeem_position.

sanity.py's ghost-redemption path was calling _redeem_position(..., paper=False),
but the function never had a `paper` parameter — so every ghost redemption threw
TypeError at runtime and winnings were never collected. Unit tests didn't catch
it because the call site is buried behind on-chain/data-API I/O. This test binds
the kwargs each caller uses against the real signature so a future mismatch fails
in CI instead of silently in production.
"""
import inspect

from polymarket.execution.redeem import _redeem_position


def test_redeem_position_rejects_paper_kwarg():
    sig = inspect.signature(_redeem_position)
    assert "paper" not in sig.parameters, (
        "_redeem_position has no 'paper' param — callers must not pass one "
        "(see sanity.py ghost-redemption regression)"
    )


def test_sanity_ghost_redemption_kwargs_bind():
    # The exact kwargs sanity._check_ghost_positions passes must bind cleanly.
    sig = inspect.signature(_redeem_position)
    sig.bind(
        condition_id="0x" + "ab" * 32,
        token_id="123456789012",
        shares=10.0,
        side="YES",
    )


def test_redeem_loop_positional_call_binds():
    # The redeem_loop call site uses positional args (condition_id, token_id,
    # shares, side) — guard that ordering too.
    sig = inspect.signature(_redeem_position)
    sig.bind("0x" + "cd" * 32, "987654321098", 5.0, "NO")
