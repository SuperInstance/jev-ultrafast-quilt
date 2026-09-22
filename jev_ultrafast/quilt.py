"""Quilt receipts for browser-agent runs — the 4quilt family recipe.

Every choice this agent makes is already typed: an operation, a target,
verified probabilities. This module books those choices as hash-chained
receipts so a run's provenance — "with this goal, on this page state, the
policy chose that operation and target, and the browser executed it" — is
verifiable by any quilt substrate after the browser session is gone.

Family doctrine (shared with laya4quilt, tagseq2tagseq4quilt, gpu_bpe4quilt):
stdlib-only, canonical JSON (sort_keys, tight separators), fnv1a-32 row
hashes chained from genesis "0"*8, named REFUSED rows, view head captured
before booking, plural exports. The envelope core (tick/ts/op/actor/payload/
chain_prev/row_hash) is family-owned; this producer adds context fields
(step, fingerprint) and the row hash binds every field present.

Content policy: decision content (operations, targets, probabilities) is
booked verbatim — it is the attested claim. Bulky or user-adjacent content
(element tables, typed form values) is bound by sha-256 — content-binding
without payload duplication; the live agent history already carries the
payloads, the receipt carries the binding.
"""

from __future__ import annotations

import hashlib
import json

GENESIS = "0" * 8
FNV1A_OFFSET = 0x811C9DC5
FNV1A_PRIME = 0x01000193
MASK32 = 0xFFFFFFFF

PRODUCER = {"tool": "jev-ultrafast-quilt", "vocabulary": "agent-run/v1"}
ENVELOPE_FIELDS = ("tick", "ts", "op", "actor", "payload", "chain_prev", "row_hash")


def fnv1a32(data: str) -> str:
    h = FNV1A_OFFSET
    for byte in data.encode("utf-8"):
        h = ((h ^ byte) * FNV1A_PRIME) & MASK32
    return f"{h:08x}"


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _book(rows, head, op, actor, payload, ts, tick, **context):
    row = {
        "tick": tick,
        "ts": ts if ts is not None else tick,
        "op": op,
        "actor": actor,
        "payload": payload,
        "chain_prev": head,
        **context,
    }
    row["row_hash"] = fnv1a32(canonical({k: v for k, v in row.items() if k != "row_hash"}))
    rows.append(row)
    return row["row_hash"]


class RunLedger:
    """Hash-chained receipts for one agent run. None of it needs a browser."""

    def __init__(self, actor: str = "jev-ultrafast-quilt", *, run_id: str | None = None):
        self.actor = actor
        self.run_id = run_id or "run"
        self.rows: list[dict] = []
        self.head = GENESIS
        self._tick = 0

    # -- lifecycle ---------------------------------------------------------

    def bind(self, goal: str, url: str, *, budgets: dict, page_fingerprint: str,
             model: str, ts=None) -> dict:
        """BIND the run: goal, start url, execution budgets, first observation."""
        self._tick = 0
        row = self._row(
            "BIND",
            {
                "producer": PRODUCER,
                "kind": "agent-run/v1",
                "goal": goal,
                "url": url,
                "budgets": dict(budgets),
                "model": model,
                "page_fingerprint": page_fingerprint,
            },
            ts,
        )
        return row

    def decision(self, step: int, page_fingerprint: str, elements, decision: dict,
                 ts=None) -> dict:
        """EFFECT: one typed choice. Rival answers are preserved verbatim."""
        elements_sha256 = sha256_hex(canonical(elements))
        row = self._row(
            "EFFECT",
            {
                "kind": "decision/v1",
                "step": step,
                "operation": decision["operation"],
                "target": decision.get("target"),
                "choice": decision["choice"],
                "operation_probabilities": dict(decision.get("operation_probabilities") or {}),
                "target_probabilities": dict(decision.get("target_probabilities") or {}),
                "confidence": decision.get("confidence"),
                "target_confidence": decision.get("target_confidence"),
                "model": decision.get("model"),
                "usage": decision.get("usage") or {},
                "latency_ms": decision.get("latency_ms"),
                "elements_sha256": elements_sha256,
            },
            ts,
            step=step,
            fingerprint=page_fingerprint,
        )
        return row

    def execution(self, step: int, page_fingerprint: str, entry: dict, ts=None) -> dict:
        """EFFECT: the browser executed a choice. Typed text is bound, not copied."""
        text = entry.get("text")
        row = self._row(
            "EFFECT",
            {
                "kind": "execution/v1",
                "step": step,
                "choice": entry["choice"],
                "action": entry["action"],
                "action_kind": entry["kind"],
                "probability": entry.get("probability"),
                "confidence": entry.get("confidence"),
                "text_sha256": sha256_hex(text) if isinstance(text, str) else None,
                "text_model": entry.get("text_helper"),
                "text_latency_ms": entry.get("text_latency_ms"),
                "page_changed": entry.get("page_changed"),
                "url": entry.get("url"),
                "executed_ms": entry.get("executed_ms"),
            },
            ts,
            step=step,
            fingerprint=page_fingerprint,
        )
        return row

    def resolution(self, status: str, *, page_fingerprint: str, detail=None, ts=None) -> dict:
        """EFFECT: terminal resolution. Only DONE resolves; status is the claim."""
        if status != "done":
            raise ValueError("resolution() is for DONE; terminal refusals use refused()")
        return self._row(
            "EFFECT",
            {"kind": "resolution/v1", "status": status, "detail": detail},
            ts,
            fingerprint=page_fingerprint,
        )

    def refused(self, reason: str, *, page_fingerprint: str | None = None,
                detail=None, ts=None) -> dict:
        """REFUSED: a named, visible refusal. The chain records why it stopped."""
        return self._row(
            "REFUSED",
            {"producer": PRODUCER, "kind": "refusal/v1", "reason": reason, "detail": detail},
            ts,
            fingerprint=page_fingerprint,
        )

    def heartbeat(self, step: int, elapsed_ms, ts=None) -> dict:
        return self._row(
            "TICK", {"kind": "heartbeat/v1", "step": step, "elapsed_ms": elapsed_ms}, ts,
            step=step,
        )

    def view(self, exports: list[dict], ts=None) -> dict:
        """VIEW: named artifacts under this chain's head. Head captured first."""
        head = self.head
        return self._row(
            "VIEW",
            {"kind": "exports/v1", "exports": [dict(e) for e in exports], "head": head},
            ts,
        )

    # -- internals ---------------------------------------------------------

    def _row(self, op, payload, ts, **context):
        self._tick += 1
        row = _book(self.rows, self.head, op, self.actor, payload, ts, self._tick, **context)
        self.head = row
        return self.rows[-1]

    # -- verification ------------------------------------------------------

    def verify(self):
        return verify_chain(self.rows)

    def export(self, path=None) -> dict:
        """Plural exports: the canon dict (head BEFORE this export's own VIEW row
        is booked by callers via view()) and, when path is given, JSONL rows."""
        doc = {"format": "quilt-agent-run/v1", "count": len(self.rows),
               "head": self.head, "rows": self.rows}
        if path:
            with open(path, "w", encoding="utf-8") as handle:
                for row in self.rows:
                    handle.write(canonical(row) + "\n")
        return doc


def verify_chain(rows: list[dict]):
    """Family verifier: envelope shape, canonical bytes, hashes, chain order.

    Never interprets payloads — storage owns facts about records; producers
    own vocabularies. Verifies rows from ANY 4quilt producer unchanged.
    """
    head = GENESIS
    for i, row in enumerate(rows):
        missing = [f for f in ENVELOPE_FIELDS if f not in row]
        if missing:
            return False, i, "MISSING_ENVELOPE_FIELDS:" + ",".join(missing)
        body = {k: v for k, v in row.items() if k != "row_hash"}
        if fnv1a32(canonical(body)) != row["row_hash"]:
            return False, i, "ROW_HASH_MISMATCH"
        if row["chain_prev"] != head:
            return False, i, f"CHAIN_BREAK_AT_ROW_{i}"
        head = row["row_hash"]
    return True, None, None
