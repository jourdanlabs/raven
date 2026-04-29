# RAVEN Structured Refusal Cookbook

When RAVEN cannot answer a query, it does not collapse every failure into a
single `REFUSED` status. Capability 1.3 introduces a typed
[`RefusalReason`](../raven/types/phase1.py) on the
[`AuroraVerdict`](../raven/types/phase1.py) returned by
`RAVENPipeline.recall_v2()`. Each refusal type maps to a different
recommended action and a different downstream pattern in your agent loop.

This cookbook covers the five refusal types. Every code example is
self-contained, executable against the public API, and exercised by the
test suite (see `tests/test_refusal.py::TestCookbookExamples`). To
re-run an example by hand:

```bash
python docs/_examples/01_insufficient_evidence.py
```

| Type                                  | Recommended action  | Pattern                                  |
| ------------------------------------- | ------------------- | ---------------------------------------- |
| `insufficient_evidence`               | `ask_user`          | Surface gap + ask for missing context    |
| `conflicting_evidence_unresolvable`   | `surface_uncertainty` | Show both sides, do not pick a winner |
| `staleness_threshold_exceeded`        | `request_context`   | Ask for fresh data or trigger re-ingest  |
| `identity_ambiguous`                  | `ask_user`          | Show candidates, ask user to pick        |
| `scope_violation`                     | `escalate`          | Route to human reviewer; do not retry    |

The CLI mirrors this table:

```bash
raven refusal-types
```

---

## 1. `insufficient_evidence`

**When this refusal fires.** The pipeline ran to completion but no candidate
memory cleared the AURORA approval threshold and no other refusal category
matched. This is the catch-all for "I looked, I didn't find anything strong
enough, and I have nothing else to flag." It fires when the store is empty,
when retrieval surfaces only weakly-related entries, or when the query is
about a topic RAVEN simply has no evidence for.

**Pattern.** Surface what RAVEN *did* see (`what_we_know`) and what it
needs (`what_we_dont`), then ask the user to either reformulate the query
or supply the missing context. Do not invent an answer.

```python
# docs/_examples/01_insufficient_evidence.py
import tempfile

from raven.pipeline import RAVENPipeline
from raven.storage.store import RAVENStore
from raven.types import AuroraVerdict, RefusalReason


def handle_insufficient_evidence(verdict: AuroraVerdict) -> str:
    assert verdict.refusal_reason is not None
    r: RefusalReason = verdict.refusal_reason
    lines = ["I don't have enough to answer that confidently."]
    lines.append("Here's what I do know:")
    for s in r.what_we_know:
        lines.append(f"  - {s}")
    lines.append("Here's what I'd need:")
    for s in r.what_we_dont:
        lines.append(f"  - {s}")
    lines.append(f"Suggested next step: {r.recommended_action}")
    return "\n".join(lines)


with tempfile.TemporaryDirectory() as tmp:
    store = RAVENStore(db_path=f"{tmp}/raven.db")
    pipeline = RAVENPipeline(store)
    verdict = pipeline.recall_v2("what is the meaning of life")
    assert verdict.refusal_reason.type == "insufficient_evidence"
    print(handle_insufficient_evidence(verdict))
```

---

## 2. `conflicting_evidence_unresolvable`

**When this refusal fires.** PULSAR detected one or more contradictions
among the candidate memories and the reconciliation hierarchy (Capability
1.1) was unable to select a winner. Until reconciliation produces a
`ResolvedClaim` for *every* contradiction, the leftover ones are treated
as unresolved and trigger this refusal.

**Pattern.** Surface both sides of the conflict to the user instead of
silently picking one. The recommended action is `surface_uncertainty` —
let the user choose which version they want to act on, or escalate to a
human if the contradiction is policy-significant.

```python
# docs/_examples/02_conflicting_evidence.py
import time

from raven.refusal import classify_refusal
from raven.types import AuroraInput, Contradiction, MemoryEntry
from raven.validation.aurora import APPROVE_THRESHOLD


e1 = MemoryEntry(id="a", text="The server always returns 200", timestamp=time.time())
e2 = MemoryEntry(id="b", text="The server never returns 200", timestamp=time.time())
contradiction = Contradiction(e1, e2, "absolutist", "always vs never", 0.9)

aurora_input = AuroraInput(
    entries=[e1, e2],
    decay_weights=[0.5, 0.5],
    importance_scores=[0.5, 0.5],
    contradictions=[contradiction],
    causal_edges=[], stale_ids=set(), meteor_entity_count=0,
)
reason = classify_refusal(
    query="does the server return 200",
    aurora_input=aurora_input,
    aurora_threshold=APPROVE_THRESHOLD,
)
assert reason.type == "conflicting_evidence_unresolvable"
assert reason.recommended_action == "surface_uncertainty"
```

---

## 3. `staleness_threshold_exceeded`

**When this refusal fires.** ECLIPSE applied decay to every candidate
memory and *every* one fell below the configured floor (default `0.05`,
the lowest of the built-in DecayPolicy floors). RAVEN has memories on
the topic, but they are all too stale to trust.

**Pattern.** The recommended action is `request_context`. Either ask the
user to supply fresh information directly, or trigger an upstream
re-ingest pipeline so a future query can succeed. Do **not** silently
serve stale answers — that violates the trust contract that drives the
refusal in the first place.

```python
# docs/_examples/03_staleness.py
import time

from raven.refusal import classify_refusal
from raven.types import AuroraInput, MemoryEntry
from raven.validation.aurora import APPROVE_THRESHOLD


e = MemoryEntry(
    id="old",
    text="server was rebooted last quarter",
    timestamp=time.time() - 200 * 86_400,
)
aurora_input = AuroraInput(
    entries=[e],
    decay_weights=[0.001],   # below default floor
    importance_scores=[0.5],
    contradictions=[], causal_edges=[], stale_ids=set(), meteor_entity_count=0,
)
reason = classify_refusal(
    query="server reboot status",
    aurora_input=aurora_input,
    aurora_threshold=APPROVE_THRESHOLD,
)
assert reason.type == "staleness_threshold_exceeded"
assert reason.recommended_action == "request_context"
```

---

## 4. `identity_ambiguous`

**When this refusal fires.** METEOR identified at least two distinct
canonical entity candidates among the candidate memories (`meteor_entity_count >= 2`
and `>= 2` distinct `entity_tags`). RAVEN cannot tell which entity the
user meant, and downstream conflict / staleness signals are not yet
meaningful until the entity is fixed.

**Pattern.** Show the candidates to the user (`what_we_know` lists them)
and ask them to disambiguate. The recommended action is `ask_user`.

```python
# docs/_examples/04_identity_ambiguous.py
import time

from raven.refusal import classify_refusal
from raven.types import AuroraInput, MemoryEntry
from raven.validation.aurora import APPROVE_THRESHOLD


e1 = MemoryEntry(id="a", text="Krillin won", timestamp=time.time(), entity_tags=["Krillin"])
e2 = MemoryEntry(id="b", text="18 won", timestamp=time.time(), entity_tags=["18"])

aurora_input = AuroraInput(
    entries=[e1, e2],
    decay_weights=[0.5, 0.5],
    importance_scores=[0.5, 0.5],
    contradictions=[], causal_edges=[], stale_ids=set(),
    meteor_entity_count=2,
)
reason = classify_refusal(
    query="who won the match",
    aurora_input=aurora_input,
    aurora_threshold=APPROVE_THRESHOLD,
)
assert reason.type == "identity_ambiguous"
assert reason.recommended_action == "ask_user"
```

---

## 5. `scope_violation`

**When this refusal fires.** The query contains content tokens (length
>= 3) that are not substrings of any entry in the operator-supplied
`scope_allowlist`. The scope check fires *before* retrieval — RAVEN
should not consult evidence for queries it is not authorized to answer.
This is a structural refusal: pass `scope_allowlist=None` to disable.

**Pattern.** The recommended action is `escalate`. Open a ticket against
your policy review process; never silently retry the query against a
different RAVEN instance without an audit trail.

```python
# docs/_examples/05_scope_violation.py
import tempfile

from raven.pipeline import RAVENPipeline
from raven.storage.store import RAVENStore


with tempfile.TemporaryDirectory() as tmp:
    store = RAVENStore(db_path=f"{tmp}/raven.db")
    pipeline = RAVENPipeline(store)
    verdict = pipeline.recall_v2(
        "tell me about quantum cryptography",
        scope_allowlist=["billing", "invoice"],
    )
    assert verdict.refusal_reason.type == "scope_violation"
    assert verdict.refusal_reason.recommended_action == "escalate"
```

---

## Recipe: a top-level router

A typical agent loop reads `verdict.refusal_reason.recommended_action`
and dispatches:

```python
def route(verdict):
    if verdict.decision == "approve":
        return ("answer", verdict)
    action = verdict.refusal_reason.recommended_action
    return {
        "ask_user":             ("disambiguate", verdict),
        "request_context":      ("ingest_more",  verdict),
        "surface_uncertainty":  ("show_both",    verdict),
        "escalate":             ("page_human",   verdict),
    }[action]
```

The `audit_hash` on every `RefusalReason` and `AuroraVerdict` is the
chain-of-custody anchor — log it alongside any downstream action so the
decision can be replayed later.
