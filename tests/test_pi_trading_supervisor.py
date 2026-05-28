import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "pi_trading_supervisor.py"
SPEC = importlib.util.spec_from_file_location("pi_trading_supervisor", MODULE_PATH)
supervisor = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = supervisor
SPEC.loader.exec_module(supervisor)


class ProtectionClient:
    def __init__(self, stop_result=None, tp_result=None):
        self.calls = []
        self.stop_result = stop_result or {"algoId": 30}
        self.tp_result = tp_result or {"algoId": 40}

    def open_algo_orders(self, symbol):
        self.calls.append(("list", symbol))
        return [
            {"symbol": symbol, "algoId": 10, "orderType": "STOP_MARKET"},
            {"symbol": symbol, "algoId": 20, "orderType": "TAKE_PROFIT_MARKET"},
        ]

    def place_hard_stop(self, symbol, side, price):
        self.calls.append(("stop", symbol, side, price))
        return self.stop_result

    def place_reduce_only_stop(self, symbol, side, price, quantity):
        self.calls.append(("quantity_stop", symbol, side, price, quantity))
        return self.stop_result

    def place_take_profit(self, symbol, side, price):
        self.calls.append(("tp", symbol, side, price))
        return self.tp_result

    def cancel_algo_order(self, symbol, algo_id):
        self.calls.append(("cancel", symbol, algo_id))
        return {"algoId": algo_id}


class TestProtectionReplacement(unittest.TestCase):
    def test_new_stop_is_created_before_old_protection_is_canceled(self):
        client = ProtectionClient()

        result = supervisor.BinanceClient.place_protection_orders(
            client, "ETHUSDT", "SELL", 99.0, 103.0, position_qty=0.1
        )

        self.assertEqual(result["stop"]["algoId"], 30)
        self.assertTrue(result["take_profit_preserved"])
        self.assertEqual(
            client.calls,
            [
                ("list", "ETHUSDT"),
                ("quantity_stop", "ETHUSDT", "SELL", 99.0, 0.1),
                ("cancel", "ETHUSDT", 10),
            ],
        )

    def test_failed_new_stop_never_cancels_existing_protection(self):
        client = ProtectionClient(stop_result={"code": -1, "msg": "rejected"})

        with self.assertRaises(RuntimeError):
            supervisor.BinanceClient.place_protection_orders(
                client, "ETHUSDT", "SELL", 99.0, 103.0, position_qty=0.1
            )

        self.assertFalse(any(call[0] == "cancel" for call in client.calls))

    def test_initial_protection_uses_close_position_orders(self):
        client = ProtectionClient()
        client.open_algo_orders = mock.Mock(return_value=[])

        result = supervisor.BinanceClient.place_protection_orders(client, "ETHUSDT", "SELL", 99.0, 103.0)

        self.assertEqual(result["stop"]["algoId"], 30)
        self.assertIn(("stop", "ETHUSDT", "SELL", 99.0), client.calls)
        self.assertIn(("tp", "ETHUSDT", "SELL", 103.0), client.calls)

    def test_initial_protection_can_skip_take_profit(self):
        client = ProtectionClient()
        client.open_algo_orders = mock.Mock(return_value=[])

        result = supervisor.BinanceClient.place_protection_orders(client, "ETHUSDT", "SELL", 99.0, 0.0)

        self.assertEqual(result["stop"]["algoId"], 30)
        self.assertIsNone(result["take_profit"])
        self.assertNotIn(("tp", "ETHUSDT", "SELL", 0.0), client.calls)


class TestGuardAndRisk(unittest.TestCase):
    def make_supervisor(self):
        sup = supervisor.TradingSupervisor.__new__(supervisor.TradingSupervisor)
        sup.live = True
        sup.risk = supervisor.RiskConfig()
        sup.log_path = Path(tempfile.mkstemp(suffix=".jsonl")[1])
        return sup

    def test_untrusted_account_snapshot_does_not_cancel_orders(self):
        sup = self.make_supervisor()
        binance = mock.Mock()
        sup.binance = binance
        snapshot = {
            "account": {"raw_ok": False},
            "positions": [],
            "all_open_orders": [{"symbol": "BTCUSDT"}],
            "all_open_algo_orders": [{"symbol": "BTCUSDT"}],
            "data_health": {"account": False, "all_open_orders": True, "all_open_algo_orders": True},
        }

        actions = sup.guard_account(snapshot)

        self.assertEqual(actions[0]["event"], "guard_skipped_untrusted_account_data")
        binance.cancel_all_orders.assert_not_called()
        binance.cancel_open_algo_orders.assert_not_called()

    def test_ai_forward_open_is_not_rejected_by_confidence_rr_or_daily_pnl(self):
        sup = self.make_supervisor()
        sup.binance = mock.Mock(filters={"ETHUSDT": {"minNotional": 5.0}})
        decision = {
            "decision": "open_long",
            "symbol": "ETHUSDT",
            "confidence": 0.01,
            "proposal": {
                "entry_price": 100.0,
                "entry_condition": "maker entry",
                "notional_usdt": 6.0,
                "leverage": 2,
                "stop_loss": 99.9,
                "take_profit": 100.01,
                "invalid_if": "below stop",
            },
        }
        snapshot = {
            "account": {"raw_ok": True, "wallet": 100.0, "available": 100.0, "positions": []},
            "positions": [],
            "data_health": {"account": True, "all_open_orders": True, "all_open_algo_orders": True},
            "all_open_orders": [],
            "all_open_algo_orders": [],
            "recent_income": [{"incomeType": "REALIZED_PNL", "income": "-100", "time": 9999999999999}],
        }

        self.assertEqual(sup.risk_check(decision, snapshot), (True, "open ok"))

    def test_open_accepts_disaster_stop_without_take_profit(self):
        sup = self.make_supervisor()
        sup.binance = mock.Mock(filters={"ETHUSDT": {"minNotional": 5.0}})
        decision = {
            "decision": "open_long",
            "symbol": "ETHUSDT",
            "proposal": {
                "entry_price": 100.0,
                "entry_condition": "maker entry",
                "notional_usdt": 6.0,
                "leverage": 2,
                "disaster_stop": 97.5,
                "take_profit": None,
                "invalid_if": "structure breaks",
            },
        }
        snapshot = {
            "account": {"raw_ok": True, "wallet": 100.0, "available": 100.0, "positions": []},
            "positions": [],
            "data_health": {"account": True, "all_open_orders": True, "all_open_algo_orders": True},
            "all_open_orders": [],
            "all_open_algo_orders": [],
            "recent_income": [],
        }

        self.assertEqual(sup.risk_check(decision, snapshot), (True, "open ok"))

    def test_same_direction_add_can_reuse_existing_protection_orders(self):
        sup = self.make_supervisor()
        sup.binance = mock.Mock(filters={"ETHUSDT": {"minNotional": 5.0}})
        position = {"symbol": "ETHUSDT", "positionAmt": "0.1", "entryPrice": "99.0", "markPrice": "101.0"}
        decision = {
            "decision": "open_long",
            "symbol": "ETHUSDT",
            "proposal": {
                "entry_price": 100.0,
                "entry_condition": "trend add",
                "notional_usdt": 6.0,
                "leverage": 2,
                "disaster_stop": 97.5,
                "invalid_if": "trend breaks",
            },
        }
        snapshot = {
            "account": {"raw_ok": True, "wallet": 100.0, "available": 100.0, "positions": [position]},
            "positions": [position],
            "data_health": {"account": True, "all_open_orders": True, "all_open_algo_orders": True},
            "all_open_orders": [],
            "all_open_algo_orders": [{"symbol": "ETHUSDT", "orderType": "STOP_MARKET", "algoId": 8, "triggerPrice": "97.0"}],
            "recent_income": [],
        }

        self.assertEqual(sup.risk_check(decision, snapshot), (True, "open ok"))

    def test_add_rejects_opposite_position(self):
        sup = self.make_supervisor()
        sup.binance = mock.Mock(filters={"ETHUSDT": {"minNotional": 5.0}})
        position = {"symbol": "ETHUSDT", "positionAmt": "-0.1", "entryPrice": "101.0", "markPrice": "100.0"}
        decision = {
            "decision": "open_long",
            "symbol": "ETHUSDT",
            "proposal": {
                "entry_price": 100.0,
                "entry_condition": "trend add",
                "notional_usdt": 6.0,
                "leverage": 2,
                "disaster_stop": 97.5,
                "invalid_if": "trend breaks",
            },
        }
        snapshot = {
            "account": {"raw_ok": True, "wallet": 100.0, "available": 100.0, "positions": [position]},
            "positions": [position],
            "data_health": {"account": True, "all_open_orders": True, "all_open_algo_orders": True},
            "all_open_orders": [],
            "all_open_algo_orders": [{"symbol": "ETHUSDT", "orderType": "STOP_MARKET", "algoId": 8, "triggerPrice": "97.0"}],
            "recent_income": [],
        }

        self.assertEqual(sup.risk_check(decision, snapshot)[1], "existing opposite position")

    def test_add_cannot_loosen_existing_stop(self):
        sup = self.make_supervisor()
        sup.binance = mock.Mock(filters={"ETHUSDT": {"minNotional": 5.0}})
        position = {"symbol": "ETHUSDT", "positionAmt": "0.1", "entryPrice": "99.0", "markPrice": "101.0"}
        decision = {
            "decision": "open_long",
            "symbol": "ETHUSDT",
            "proposal": {
                "entry_price": 100.0,
                "entry_condition": "trend add",
                "notional_usdt": 6.0,
                "leverage": 2,
                "disaster_stop": 96.5,
                "invalid_if": "trend breaks",
            },
        }
        snapshot = {
            "account": {"raw_ok": True, "wallet": 100.0, "available": 100.0, "positions": [position]},
            "positions": [position],
            "data_health": {"account": True, "all_open_orders": True, "all_open_algo_orders": True},
            "all_open_orders": [],
            "all_open_algo_orders": [{"symbol": "ETHUSDT", "orderType": "STOP_MARKET", "algoId": 8, "triggerPrice": "97.0"}],
            "recent_income": [],
        }

        self.assertEqual(sup.risk_check(decision, snapshot)[1], "add disaster_stop would loosen existing stop")

    def test_open_still_requires_explicit_entry(self):
        sup = self.make_supervisor()
        sup.binance = mock.Mock(filters={"ETHUSDT": {"minNotional": 5.0}})
        decision = {
            "decision": "open_long",
            "symbol": "ETHUSDT",
            "proposal": {
                "entry_condition": "maker entry",
                "notional_usdt": 6.0,
                "leverage": 2,
                "stop_loss": 99.0,
                "take_profit": 102.0,
                "invalid_if": "below stop",
            },
        }
        snapshot = {
            "account": {"raw_ok": True, "wallet": 100.0, "available": 100.0, "positions": []},
            "data_health": {"account": True, "all_open_orders": True, "all_open_algo_orders": True},
            "all_open_orders": [],
            "all_open_algo_orders": [],
            "recent_income": [],
        }

        self.assertEqual(sup.risk_check(decision, snapshot)[1], "open missing entry_price/entry_zone")

    def test_tighten_stop_uses_position_mark_price(self):
        sup = self.make_supervisor()
        sup.binance = mock.Mock(filters={"HYPEUSDT": {"minNotional": 5.0}})
        decision = {"decision": "tighten_stop", "symbol": "HYPEUSDT", "proposal": {"new_stop": 61.92}}
        position = {"symbol": "HYPEUSDT", "positionAmt": "0.11", "entryPrice": "61.62", "markPrice": "62.24"}
        snapshot = {"account": {"raw_ok": True, "positions": [position]}, "data_health": {"account": True}}

        self.assertEqual(sup.risk_check(decision, snapshot), (True, "position adjustment ok"))
        position.pop("markPrice")
        self.assertEqual(sup.risk_check(decision, snapshot)[1], "mark price unavailable for stop adjustment")


class TestOrderExecution(unittest.TestCase):
    def test_partial_fill_cancels_remainder_instead_of_waiting_unprotected(self):
        class PartialFillClient:
            def public_api(self, endpoint, params):
                return {"bids": [["100", "1"]], "asks": [["101", "1"]]}

            def place_limit_maker_order(self, symbol, side, qty, price, reduce_only=False):
                return {"orderId": 7}

            def order_status(self, symbol, order_id):
                return {"status": "PARTIALLY_FILLED", "executedQty": "0.01"}

            def cancel_order(self, symbol, order_id):
                return {"orderId": order_id, "status": "CANCELED"}

        with mock.patch.object(supervisor.time, "sleep", return_value=None):
            result = supervisor.BinanceClient.place_limit_entry_with_wait(
                PartialFillClient(), "ETHUSDT", "BUY", 0.1, wait_seconds=35, entry_price=100.0
            )

        self.assertEqual(result["last"]["status"], "PARTIALLY_FILLED")
        self.assertEqual(result["cancel"]["status"], "CANCELED")

    def test_failed_close_does_not_cancel_protection(self):
        sup = supervisor.TradingSupervisor.__new__(supervisor.TradingSupervisor)
        sup.live = True
        sup.binance = mock.Mock()
        sup.binance.close_position_market.return_value = {"code": -1, "msg": "rejected"}
        snapshot = {"account": {"positions": [{"symbol": "ETHUSDT", "positionAmt": "1"}]}}

        with self.assertRaises(RuntimeError):
            sup.execute({"decision": "close", "symbol": "ETHUSDT"}, snapshot, "approved")

        sup.binance.cancel_open_algo_orders.assert_not_called()

    def test_breakeven_move_honors_ai_tighter_stop(self):
        sup = supervisor.TradingSupervisor.__new__(supervisor.TradingSupervisor)
        sup.live = True
        sup.log_path = Path(tempfile.mkstemp(suffix=".jsonl")[1])
        sup.binance = mock.Mock()
        existing = [{"orderType": "STOP_MARKET", "side": "SELL", "triggerPrice": "60.92"}]
        sup.binance.open_algo_orders.return_value = existing
        sup.binance.place_protection_orders.return_value = {"stop": {"algoId": 8}}
        snapshot = {"account": {"positions": [{"symbol": "HYPEUSDT", "positionAmt": "0.11", "entryPrice": "61.62"}]}}
        decision = {
            "decision": "move_stop_to_breakeven",
            "symbol": "HYPEUSDT",
            "proposal": {"new_stop": 61.72, "take_profit": 62.75},
        }

        sup.execute(decision, snapshot, "approved")

        sup.binance.place_protection_orders.assert_called_once_with(
            "HYPEUSDT",
            "SELL",
            61.72,
            62.75,
            position_qty=0.11,
            existing_orders=existing,
        )

    def test_add_refreshes_total_position_before_replacing_stop(self):
        sup = supervisor.TradingSupervisor.__new__(supervisor.TradingSupervisor)
        sup.live = True
        sup.risk = supervisor.RiskConfig()
        sup.log_path = Path(tempfile.mkstemp(suffix=".jsonl")[1])
        sup.binance = mock.Mock()
        sup.binance.filters = {"ETHUSDT": {"minNotional": 5.0}}
        sup.binance.qty_for_notional.return_value = 0.06
        sup.binance.set_leverage.return_value = {"leverage": 2}
        sup.binance.place_limit_entry_with_wait.return_value = {"executedQty": "0.06"}
        sup.binance.account_state.return_value = {
            "positions": [{"symbol": "ETHUSDT", "positionAmt": "0.16", "entryPrice": "100.0"}]
        }
        existing = [{"symbol": "ETHUSDT", "orderType": "STOP_MARKET", "algoId": 8}]
        sup.binance.open_algo_orders.return_value = existing
        sup.binance.place_protection_orders.return_value = {"stop": {"algoId": 9}}
        snapshot = {
            "account": {"positions": [{"symbol": "ETHUSDT", "positionAmt": "0.1", "entryPrice": "99.0"}]},
        }
        decision = {
            "decision": "open_long",
            "symbol": "ETHUSDT",
            "proposal": {
                "entry_price": 100.0,
                "entry_condition": "trend add",
                "notional_usdt": 6.0,
                "leverage": 2,
                "disaster_stop": 97.5,
                "take_profit": None,
                "invalid_if": "trend breaks",
            },
        }

        sup.execute(decision, snapshot, "approved")

        sup.binance.place_protection_orders.assert_called_once_with(
            "ETHUSDT",
            "SELL",
            97.5,
            0.0,
            position_qty=0.16,
            existing_orders=existing,
        )


class TestCycleState(unittest.TestCase):
    def test_review_prompt_contains_refreshed_protection_orders(self):
        sup = supervisor.TradingSupervisor.__new__(supervisor.TradingSupervisor)
        snapshot = {
            "time": "now",
            "mode": "LIVE",
            "account": {},
            "positions": [],
            "all_open_orders": [],
            "all_open_algo_orders": [{"orderType": "STOP_MARKET", "triggerPrice": "61.72"}],
            "data_health": {"all_open_algo_orders": True},
            "recent_income": [],
            "recent_trades": [],
            "market_overview_all_symbols": {},
            "market_indicators": {},
            "market_microstructure": {},
            "candidates": [],
            "news": [],
            "macro_context_fred": {},
        }

        prompt = sup.make_review_prompt(snapshot, None, None)

        self.assertIn('"all_open_algo_orders"', prompt)
        self.assertIn('"triggerPrice": "61.72"', prompt)

    def test_post_execution_review_receives_refreshed_orders(self):
        sup = supervisor.TradingSupervisor.__new__(supervisor.TradingSupervisor)
        sup.log_path = Path(tempfile.mkstemp(suffix=".jsonl")[1])
        sup.last_pi_call = 0.0
        sup.pi_interval = 60
        sup.stop_event = supervisor.threading.Event()
        sup.execution_lock = supervisor.threading.RLock()
        initial = {"account": {"wallet": 1}, "positions": [], "candidates": [], "news": []}
        before = {"account": {"wallet": 1}, "positions": [], "all_open_algo_orders": [{"triggerPrice": "60.92"}]}
        after = {"account": {"wallet": 1}, "positions": [], "all_open_algo_orders": [{"triggerPrice": "61.72"}]}
        decision = {"decision": "tighten_stop", "symbol": "HYPEUSDT", "proposal": {}}
        sup.build_snapshot = mock.Mock(return_value=initial)
        sup.save_state = mock.Mock()
        sup.ask_pi = mock.Mock(return_value=decision)
        sup.refresh_account_before_execution = mock.Mock(side_effect=[before, after])
        sup.risk_check = mock.Mock(return_value=(True, "approved"))
        sup.execute = mock.Mock(return_value={"event": "executed_stop_update"})
        sup.review_with_pi = mock.Mock()

        sup.cycle(force_pi=True)

        sup.review_with_pi.assert_called_once_with(
            after,
            decision,
            "approved",
            execution_result={"event": "executed_stop_update"},
            force=True,
        )
        sup.save_state.assert_called_with(after, decision)

    def test_shutdown_after_prompt_before_lock_skips_risk_and_execution(self):
        sup = supervisor.TradingSupervisor.__new__(supervisor.TradingSupervisor)
        sup.log_path = Path(tempfile.mkstemp(suffix=".jsonl")[1])
        sup.last_pi_call = 0.0
        sup.pi_interval = 60
        sup.stop_event = supervisor.threading.Event()
        initial = {"account": {"wallet": 1}, "positions": [], "candidates": [], "news": []}
        decision = {"decision": "close", "symbol": "HYPEUSDT", "proposal": {}}

        class SignalOnEnter:
            def __enter__(inner_self):
                sup.stop_event.set()

            def __exit__(inner_self, exc_type, exc, tb):
                return False

        sup.execution_lock = SignalOnEnter()
        sup.build_snapshot = mock.Mock(return_value=initial)
        sup.save_state = mock.Mock()
        sup.ask_pi = mock.Mock(return_value=decision)
        sup.refresh_account_before_execution = mock.Mock()
        sup.risk_check = mock.Mock()
        sup.execute = mock.Mock()

        sup.cycle(force_pi=True)

        sup.refresh_account_before_execution.assert_not_called()
        sup.risk_check.assert_not_called()
        sup.execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
