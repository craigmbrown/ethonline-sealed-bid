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

Day 2 (2026-09-05). The confidential workflow seals both reserves inside `handlerInTee`, emits a signed
attestation, and on `SETTLE` the DON writes the clearing price + salted commitments to
`SealedBidReceiver` on Base Sepolia — proven end to end (`EVIDENCE.md`: settlement tx
[`0x6a02d0ca…34c9`](https://sepolia.basescan.org/tx/0x6a02d0ca2ab8bee53f2745047ffdfce8c1ee45f0cac35d1bd7d37a9196c834c9)).
A `NO_OVERLAP` run sends no transaction, and the receiver reverts anything that is not a `SETTLE`.
Next: the paid BlindOracle bracket (`bo_client.py`), the two-agent demo driver, video.

See `SPEC.md` for the design and task list, `DISCLOSURE.md` for the pre-existing-work and
AI-assistance statement.

## Layout

```
sealed-bid-ts/            CRE confidential workflow (sealing + overlap + on-chain write) + 34 tests
contracts/                SealedBidReceiver.sol (Foundry) + 13 tests; two instances on Base Sepolia
scripts/deploy_receiver.py  deploys the receiver (forge artifact + web3)
bo_client.py              thin client for the public BlindOracle API        (Task 5)
scripts/skucheck.py       smoke test that the paid-service path is live      (Task 5)
scripts/demo.py           end-to-end two-agent demo driver                   (Task 9)
project.yaml              CRE targets (Base Sepolia settlement; simulation-settings for --broadcast)
secrets.yaml              secret NAME mapping — no values
```

## Running

Requires `bun`, Chainlink CRE CLI v1.31.0, Foundry, Python 3.11.

```bash
cd sealed-bid-ts && bun install && bun test          # 34 tests incl. the no-leak negative controls
cd contracts && forge test                            # 13 tests incl. a fuzz over the midpoint
# simulate (see sealed-bid-ts/README.md for the exact env vars and the --broadcast form)
cre workflow simulate ./sealed-bid-ts --target=staging-settings --non-interactive --trigger-index 0
```

## License

MIT — see `LICENSE`.
