# Sealed-Bid Agent Settlement

**ETHOnline 2026 · Classic track · Chainlink "Best Confidential Workflow"**

Two autonomous agents each hold a reserve price. Neither wants to reveal it — not to the
counterparty, not to the operator, not to a middleman. Today's agent-to-agent commerce forces
a bad trade: reveal your number, or trust someone who sees both.

This project seals both reserve prices inside a Trusted Execution Environment using
**Chainlink CRE Confidential Workflows**. The enclave returns exactly one of two things:

```
SETTLE @ <clearing price>      (midpoint of the overlap)
NO_OVERLAP                     (and nothing else — no hint of either band)
```

If the bands overlap, settlement executes on **Base**. If not, nothing leaks.

Settlement is not self-certified. Every run is bracketed by real, paid calls to an
independent third-party service ([BlindOracle](https://api.craigmbrown.com/v1/services)):
counterparty vetting before bidding, a proof pair around the run, a process-followed
attestation after it, and — the novel part — a **neutral adjudication service for contested
outcomes**, so that neither party *and neither operator* decides a dispute unilaterally.
Evidence submitted to the arbiter is the enclave's attestation plus a hash, never the
reserve price itself.

## Status

Day 1 (2026-09-04). Repository initialised. See `SPEC.md` for the design and the
task list, and `DISCLOSURE.md` for the pre-existing-work and AI-assistance statement.

## Layout (planned)

```
sealed-bid-ts/        CRE confidential workflow (sealing + overlap) + tests
bo_client.py          thin client for the public BlindOracle API
scripts/skucheck.py   smoke test that the paid-service path is live
scripts/demo.py       end-to-end two-agent demo driver
project.yaml          CRE target config (Base Sepolia)
secrets.yaml          secret NAME mapping — no values
```

## Running

Coming with Phase 1. Requires `bun`, Chainlink CRE CLI v1.31.0, Python 3.11.

## License

MIT — see `LICENSE`.
