"""Build a receipted run ledger offline — no browser, no API keys.

Replays a canned two-step trace through RunLedger the way Agent wiring does,
then verifies and exports. Shows the receipt shape a live run produces.

Run: python examples/receipt_offline_run.py --check
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from jev_ultrafast.quilt import RunLedger, canonical, sha256_hex

TRACE = [
    {
        "fingerprint": "fp-step-1",
        "elements": [{"index": "1", "label": "Where from?", "operations": ["TYPE_TEXT"]}],
        "decision": {
            "operation": "TYPE_TEXT", "target": "1", "choice": "e1",
            "operation_probabilities": {"TYPE_TEXT": 0.93, "CLICK": 0.05, "DONE": 0.02},
            "target_probabilities": {"1": 0.93, "2": 0.07},
            "confidence": 0.93, "target_confidence": 0.93,
            "model": "jev-latest", "usage": {"tokens": 120}, "latency_ms": 210,
        },
        "execution": {
            "choice": "e1", "action": "Where from?", "kind": "fill", "probability": 0.93,
            "confidence": 0.93, "text": "Zürich", "text_helper": "mercury-2.5",
            "text_latency_ms": 480, "page_changed": True,
            "url": "https://www.google.com/travel/flights?hl=en", "executed_ms": 700,
        },
    },
    {
        "fingerprint": "fp-step-2",
        "elements": [{"index": "1", "label": "Search", "operations": ["CLICK"]}],
        "decision": {
            "operation": "CLICK", "target": "1", "choice": "e2",
            "operation_probabilities": {"CLICK": 0.91, "TYPE_TEXT": 0.03, "WAIT": 0.06},
            "target_probabilities": {"1": 0.91},
            "confidence": 0.91, "target_confidence": 0.91,
            "model": "jev-latest", "usage": {"tokens": 98}, "latency_ms": 190,
        },
        "execution": {
            "choice": "e2", "action": "Search", "kind": "click", "probability": 0.91,
            "confidence": 0.91, "text": None, "text_helper": None,
            "text_latency_ms": 0, "page_changed": True,
            "url": "https://www.google.com/travel/flights/search?tfs=...", "executed_ms": 890,
        },
    },
]


def main():
    ledger = RunLedger(run_id="offline-trace")
    ledger.bind(
        "Find one-way flights from Zürich to London on September 20, 2026",
        "https://www.google.com/travel/flights?hl=en",
        budgets={"max_steps": 60, "model_call_budget": 120},
        page_fingerprint="fp-step-0",
        model="jev-latest",
    )
    for step, event in enumerate(TRACE, start=1):
        ledger.decision(step, event["fingerprint"], event["elements"], event["decision"])
        ledger.execution(step, event["fingerprint"], event["execution"])
        ledger.heartbeat(step, event["execution"]["executed_ms"])
    ledger.resolution("done", page_fingerprint="fp-step-2")
    ledger.view([{"path": "trace.jsonl", "sha256": sha256_hex(canonical(TRACE))}])

    ok, bad, why = ledger.verify()
    ops = [row["op"] for row in ledger.rows]
    print("rows:", len(ledger.rows), "ops:", " ".join(ops))
    print("head:", ledger.head, "verify:", ok, why or "")

    if "--check" in sys.argv:
        if not ok or "REFUSED" in ops:
            print("CHECK FAILED")
            return 1
        print("CHECK OK: %d receipted decisions, chain verifies, no refusals on the happy path" % len(TRACE))
    return 0


if __name__ == "__main__":
    sys.exit(main())
