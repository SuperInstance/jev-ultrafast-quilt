# Quilt receipts for browser-agent runs

Adapts jev-ultrafast for the quilt ecosystem. This agent's core claim is
**"typed choices, observable state"** — and a typed choice is exactly what a
quilt receipt books. This module gives every run a hash-chained decision
ledger: after the browser session is gone, anyone can verify *what was
decided, on what observation, and what executed*.

## The mapping (5-opcode family)

| Agent concept | quilt operation |
|---|---|
| goal + start page + budgets | **BIND** — `RunLedger.bind()` |
| each `choose()` (operation + target heads) | **EFFECT** `decision/v1` — rival head answers preserved verbatim |
| each executed action | **EFFECT** `execution/v1` — typed text bound by sha-256, not copied |
| DONE | **EFFECT** `resolution/v1` — the only terminal claim |
| BLOCKED | **REFUSED** `agent_blocked` — the agent's own vocabulary has a refusal op; the receipt honors it |
| stale page invalidates a decision | **REFUSED** `stale_decision` — decision recorded, then refused; chain continues |
| act without a fresh decision | **REFUSED** `no_decision_to_act` — a retry cannot double-click |
| text helper fails | **REFUSED** `text_generation_failed` — nothing typed, receipt says so |
| 60-step / 120-call budgets | **REFUSED** `budget_exhausted` |
| 3 no-change actions | **REFUSED** `repetition_guard` |
| exported trace/screenshots | **VIEW** — artifact paths + sha-256 under the chain head |

## Why rivals are preserved

The policy already answers every target head speculatively (`operation`,
`click_target`, `type_text_target`, …) in one round trip, and only the head
matching the chosen operation can execute. The receipt books **all** head
answers verbatim — the unexecuted heads are rival interpretations that died
on execution, and deleting them would make the receipt unable to answer
"what else was the policy considering?" (quilt doctrine: resolution cites
rivals, deletes nothing).

## Why text is hashed, not copied

Typed form values are user-adjacent content. The live `history` already
carries them; the ledger binds `text_sha256` — content-binding without
payload duplication. Decision content (operations, targets, probabilities)
*is* the attested claim and is booked verbatim.

## Usage

```python
from jev_ultrafast import Agent, RunLedger

ledger = RunLedger(run_id="flights-2026-09-22")
with Agent(url, goal, ledger=ledger) as agent:
    for state in agent.run():
        ...

ok, bad_row, why = ledger.verify()
doc = ledger.export("receipts/run.jsonl")   # canon dict + JSONL rows
```

`verify_chain(rows)` also verifies rows from the other 4quilt producers
without interpreting their payloads — the test suite freezes real
laya4quilt, tagseq2tagseq4quilt, and gpu_bpe4quilt rows as fixtures and
checks them here unmodified.

## Honest scope

The loopback demo (`demo.py`) books a `RunLedger` for every run: `reset`
starts a fresh chain (run_id = 8 hex), every step books through the
Agent, and reset/close exports a verified JSONL chain plus a canon JSON
(with the verify verdict, including bad row + reason on tamper) to
`artifacts/receipts/`. The API state exposes `ledger_head` and
`ledger_rows` so the UI can show the chain growing. Screenshots remain
content-bound: the `decision/v1` row carries `elements_sha256`, and
`VIEW` rows bind exported artifacts by hash. Real-browser runs still need
Chrome + a TypeSafe key (untouched by the receipt layer).
