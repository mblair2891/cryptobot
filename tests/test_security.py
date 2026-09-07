from __future__ import annotations

import pytest

from aethergrid.security import SecurityError, assert_no_withdraw_surface


def test_blocks_withdraw_paths() -> None:
    with pytest.raises(SecurityError):
        assert_no_withdraw_surface("/api/v3/brokerage/accounts/withdraw")
    with pytest.raises(SecurityError):
        assert_no_withdraw_surface("create_convert_quote")
    with pytest.raises(SecurityError):
        assert_no_withdraw_surface("/transfers")
    assert_no_withdraw_surface("create_order")
    assert_no_withdraw_surface("cancel_orders")
