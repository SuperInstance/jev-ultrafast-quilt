"""Quilt receipts for agent runs: offline contracts, no browser, no paid APIs."""

import json
import time
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import pytest

from jev_ultrafast import agent as loop
from jev_ultrafast.quilt import GENESIS, RunLedger, canonical, sha256_hex, verify_chain

FIXTURES = Path(__file__).parent / "fixtures"


def page():
    state = {
        "url": "https://example.test/",
        "title": "Search",
        "text": "Search",
        "scroll": {"y": 0},
        "actions": [
            {"id": "e1", "kind": "fill", "label": "Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e2", "kind": "click", "label": "Open Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e3", "kind": "click", "label": "Go", "role": "button", "value": "", "node": 20},
            {"id": "wait", "kind": "wait", "label": "Wait"},
        ],
    }
    from jev_ultrafast.browser import fingerprint

    state["fingerprint"] = fingerprint(state)
    return state


def decision(action="e1", operation="TYPE_TEXT"):
    return {
        "choice": action,
        "operation": operation,
        "target": "1" if operation == "TYPE_TEXT" else None,
        "confidence": 0.9,
        "probabilities": {action: 1.0},
        "operation_probabilities": {operation: 0.9, "DONE": 0.1},
        "target_probabilities": {"1": 0.9, "2": 0.1} if operation == "TYPE_TEXT" else {},
        "target_confidence": 0.9 if operation == "TYPE_TEXT" else None,
        "model": "test",
        "usage": {"tokens": 42},
        "latency_ms": 10,
    }


@pytest.fixture
def runner():
    a = loop.Agent.__new__(loop.Agent)
    a.screenshots = False
    a.pending_text = None
    a.ledger = None
    p = page()
    a.state = {
        "browser": Mock(fresh=Mock(return_value=True), observe=Mock(return_value=p)),
        "page": p,
        "decision": None,
        "goal": "Find a book",
        "history": [],
        "decisions": [],
        "status": "ready",
        "started_at": time.perf_counter(),
        "record": False,
        "text_calls": [],
    }
    return a


@pytest.fixture
def ledger():
    led = RunLedger(run_id="test-run")
    led.bind(
        "Find a book",
        "https://example.test/",
        budgets={"max_steps": 60, "model_call_budget": 120},
        page_fingerprint=page()["fingerprint"],
        model="test",
    )
    return led


# -- lifecycle ---------------------------------------------------------------


def test_bind_books_first_row_chained_from_genesis(ledger):
    row = ledger.rows[0]
    assert row["op"] == "BIND"
    assert row["chain_prev"] == GENESIS
    assert row["payload"]["goal"] == "Find a book"
    assert row["payload"]["budgets"]["max_steps"] == 60
    assert row["payload"]["page_fingerprint"] == page()["fingerprint"]
    ok, bad, why = ledger.verify()
    assert ok, why


def test_decision_preserves_rival_answers_verbatim(ledger):
    d = decision()
    ledger.decision(1, page()["fingerprint"], [{"index": "1"}], d)
    payload = ledger.rows[-1]["payload"]
    assert payload["kind"] == "decision/v1"
    assert payload["operation_probabilities"] == {"TYPE_TEXT": 0.9, "DONE": 0.1}
    assert payload["target_probabilities"] == {"1": 0.9, "2": 0.1}
    assert payload["elements_sha256"] == sha256_hex(canonical([{"index": "1"}]))
    assert ledger.rows[-1]["step"] == 1
    assert ledger.rows[-1]["fingerprint"] == page()["fingerprint"]


def test_execution_binds_text_by_hash_not_content(ledger):
    entry = {
        "choice": "e1", "action": "Search", "kind": "fill", "probability": 1.0,
        "confidence": 0.9, "text": "Zürich", "text_helper": "test-model",
        "text_latency_ms": 12, "page_changed": True, "url": "https://example.test/?q=1",
        "executed_ms": 55,
    }
    ledger.execution(1, page()["fingerprint"], entry)
    payload = ledger.rows[-1]["payload"]
    assert payload["kind"] == "execution/v1"
    assert payload["text_sha256"] == sha256_hex("Zürich")
    assert "text" not in payload or payload.get("text") is None
    assert "Zürich" not in canonical(ledger.rows)


def test_view_head_captured_before_booking(ledger):
    head_before = ledger.head
    ledger.view([{"path": "trace.jsonl", "sha256": "ab" * 32}])
    view = ledger.rows[-1]
    assert view["op"] == "VIEW"
    assert view["payload"]["head"] == head_before
    assert view["chain_prev"] == head_before


def test_terminal_done_resolves_and_blocked_refuses(ledger):
    ledger.resolution("done", page_fingerprint=page()["fingerprint"])
    assert ledger.rows[-1]["op"] == "EFFECT"
    assert ledger.rows[-1]["payload"]["status"] == "done"
    ledger.refused("agent_blocked", page_fingerprint=page()["fingerprint"])
    assert ledger.rows[-1]["op"] == "REFUSED"
    assert ledger.rows[-1]["payload"]["reason"] == "agent_blocked"
    with pytest.raises(ValueError):
        ledger.resolution("blocked", page_fingerprint=page()["fingerprint"])


def test_tamper_drill_fails_verification(ledger):
    ledger.decision(1, page()["fingerprint"], [], decision())
    rows = deepcopy(ledger.rows)
    rows[1]["payload"]["confidence"] = 0.1
    ok, bad, why = verify_chain(rows)
    assert not ok and why == "ROW_HASH_MISMATCH"
    # CHAIN_BREAK needs individually-valid rows in the wrong order: splice in a
    # foreign row whose hash is genuine but whose chain_prev is not our head.
    other = RunLedger()
    other.bind("other goal", "https://other.test/", budgets={"max_steps": 1},
               page_fingerprint="fp", model="test")
    foreign = deepcopy(other.rows[0])
    assert foreign["row_hash"] != ledger.rows[1]["row_hash"]
    ok, bad, why = verify_chain([ledger.rows[0], foreign])
    assert not ok and why == "CHAIN_BREAK_AT_ROW_1"


def test_export_roundtrip_jsonl(tmp_path, ledger):
    ledger.decision(1, page()["fingerprint"], [], decision())
    path = tmp_path / "run.jsonl"
    doc = ledger.export(path)
    assert doc["head"] == ledger.head
    loaded = [json.loads(line) for line in path.read_text().splitlines()]
    assert loaded == ledger.rows
    ok, _, why = verify_chain(loaded)
    assert ok, why


# -- loop wiring (the agent books what the loop already decides) -------------


def test_predict_books_decision_row(runner, ledger, monkeypatch):
    monkeypatch.setattr(loop, "choose", Mock(return_value=decision("e3", "CLICK")))
    runner.ledger = ledger
    runner.command("predict", {})
    row = ledger.rows[-1]
    assert row["op"] == "EFFECT" and row["payload"]["kind"] == "decision/v1"
    assert row["payload"]["choice"] == "e3"
    assert row["payload"]["operation_probabilities"]["CLICK"] == 0.9


def test_done_path_books_resolution(runner, ledger):
    runner.ledger = ledger
    runner.state["decision"] = decision("DONE", "DONE")
    runner.state["status"] = "predicted"
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["status"] == "done"
    assert ledger.rows[-1]["payload"]["kind"] == "resolution/v1"


def test_blocked_path_books_named_refusal(runner, ledger):
    runner.ledger = ledger
    runner.state["decision"] = decision("BLOCKED", "BLOCKED")
    runner.state["status"] = "predicted"
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["status"] == "blocked"
    assert ledger.rows[-1]["op"] == "REFUSED"
    assert ledger.rows[-1]["payload"]["reason"] == "agent_blocked"


def test_stale_decision_books_refusal_and_chain_continues(runner, ledger, monkeypatch):
    monkeypatch.setattr(loop, "choose", Mock(return_value=decision("e3", "CLICK")))
    runner.ledger = ledger
    runner.state["browser"].fresh.side_effect = loop.StalePage("navigating")
    snapshot = runner.command("tick", {})
    assert snapshot["status"] == "ready"
    assert ledger.rows[-1]["op"] == "REFUSED"
    assert ledger.rows[-1]["payload"]["reason"] == "stale_decision"
    ok, _, why = ledger.verify()
    assert ok, why


def test_act_without_decision_books_refusal(runner, ledger):
    runner.ledger = ledger
    runner.state["decision"] = None
    with pytest.raises(ValueError, match="Observe and choose"):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert ledger.rows[-1]["op"] == "REFUSED"
    assert ledger.rows[-1]["payload"]["reason"] == "no_decision_to_act"


def test_text_failure_books_refusal_nothing_typed(runner, ledger, monkeypatch):
    monkeypatch.setattr(loop, "field_text", Mock(side_effect=ValueError("nothing typed")))
    runner.ledger = ledger
    runner.state["decision"] = decision("e1", "TYPE_TEXT")
    runner.state["status"] = "predicted"
    with pytest.raises(ValueError, match="nothing typed"):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert ledger.rows[-1]["op"] == "REFUSED"
    assert ledger.rows[-1]["payload"]["reason"] == "text_generation_failed"
    runner.state["browser"].act.assert_not_called()


def test_execution_and_repetition_guard_are_booked(runner, ledger):
    runner.ledger = ledger
    for _ in range(3):
        runner.state["decision"] = decision("e3", "CLICK")
        runner.state["status"] = "predicted"
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    kinds = [r["payload"].get("kind") for r in ledger.rows if r["op"] == "EFFECT"]
    assert "execution/v1" in kinds
    assert runner.state["status"] == "blocked"
    assert ledger.rows[-1]["op"] == "REFUSED"
    assert ledger.rows[-1]["payload"]["reason"] == "repetition_guard"


def test_full_run_chain_verifies(runner, ledger, monkeypatch):
    monkeypatch.setattr(loop, "choose", Mock(return_value=decision("e3", "CLICK")))
    runner.ledger = ledger
    runner.state["status"] = "ready"
    runner.command("tick", {})
    runner.state["decision"] = decision("DONE", "DONE")
    runner.state["status"] = "predicted"
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    ops = [r["op"] for r in ledger.rows]
    assert ops[0] == "BIND"
    assert "EFFECT" in ops and "REFUSED" not in ops
    ok, bad, why = ledger.verify()
    assert ok, why


# -- cross-repo family contract ----------------------------------------------


@pytest.mark.parametrize("fixture", ["laya4quilt_rows.json", "tagseq_fabric_rows.json", "gpu_bpe_rows.json"])
def test_sibling_family_rows_verify_unmodified(fixture):
    rows = json.loads((FIXTURES / fixture).read_text())
    ok, bad, why = verify_chain(rows)
    assert ok, f"{fixture} broke: {why}"
    tampered = deepcopy(rows)
    tampered[0]["payload"] = {"different": "claim"}
    ok, bad, why = verify_chain(tampered)
    assert not ok and why == "ROW_HASH_MISMATCH"
