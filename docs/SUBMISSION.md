# ETHGlobal submission text — Sealed-Bid Agent Settlement

Copy-paste source for the ETHOnline 2026 submission form. Every claim below is backed by a
transaction or a test named in `EVIDENCE.md`.

## Project name
Sealed-Bid Agent Settlement

## Tagline (≤ 100 chars)
Two agents trade without revealing their reserve prices: sealed in a Chainlink CRE enclave, settled on Base.

## Track / prizes
- Classic — Start from Scratch
- Chainlink: **Best Confidential Workflow** (`handlerInTee`, two reserve prices as Vault secrets, overlap computed in-enclave, CRE CLI simulation evidence + a Base Sepolia write)

## Short description (≤ 280 chars)
Agent A and Agent B each hold a private reserve. A CRE Confidential Workflow reads both as Vault secrets inside a Nitro enclave and returns only SETTLE @ midpoint or NO_OVERLAP. SETTLE is written to a receiver on Base Sepolia; NO_OVERLAP writes nothing. Third-party vetting via x402.

## Description

Agent-to-agent commerce has a bad default: to negotiate, an agent either reveals its reserve price
(and gets priced at the edge of it) or trusts a middleman who sees both numbers. We remove the
middleman with a Chainlink CRE **Confidential Workflow**.

Both reserve prices and two commitment salts are CRE Vault secrets. Inside a `handlerInTee` handler
running on AWS Nitro, the workflow makes one `getSecrets` call, computes whether the bands overlap,
and produces exactly one of two outputs: `SETTLE @ clearing` (the midpoint) or the literal
`NO_OVERLAP`. Alongside it: two salted commitments `sha256(reserve‖salt)` and a run id, so either
party can later prove what it bid without the enclave ever having published it. Nothing else crosses
back to the DON — not the reserves, not the gap, not which side was higher. That property is tested at
every layer, with deliberately leaky implementations kept in the suite as negative controls.

On `SETTLE`, the DON writes the attestation through the CRE Forwarder to `SealedBidReceiver` on Base
Sepolia, a contract that records only SETTLE outcomes and reverts everything else — so even a
misbehaving workflow could not leave a NO_OVERLAP trace. Agent A then pays Agent B the clearing amount.
On `NO_OVERLAP`, no transaction is sent at all.

Settlement is not self-certified. Before bidding, each agent vets the other through BlindOracle, a
public pay-per-call API settled over x402 in USDC on Base mainnet; a trust badge opens the bracket, a
reputation lookup closes it, and a process-followed attestation can be bought over the hash-chained,
signed evidence of the run. Every one of those payments is a USDC transfer anyone can check on Basescan.

The one thing `SETTLE` does reveal is `A_max + B_min`. That is the standard sealed-bid trade-off and
we state it rather than hide it.

## How it's made

- **Chainlink CRE** (CLI v1.31.0, `@chainlink/cre-sdk` 1.18.0, TypeScript). `cre.handlerInTee` with a
  Nitro/us-west-2 constraint; one `getSecrets` call for four Vault secrets (a measured runtime limit —
  a second call fails); `usingTheDons().report(...)` with an ABI-encoded attestation; `EVMClient.writeReport`
  on SETTLE only. 34 bun tests. Measured along the way: `cre workflow simulate --broadcast` writes through
  the simulator's own mock forwarder as a placeholder owner, so a receiver built for the production
  Forwarder correctly rejects it — hence two deployed receivers, one per forwarder, documented in EVIDENCE.md.
- **Solidity / Foundry.** `SealedBidReceiver` implements CRE's `IReceiver`; forwarder-only, optional
  pinned workflow owner, SETTLE-only state, ERC-165. 13 forge tests including a fuzz over the midpoint.
- **Base.** Settlement on Base Sepolia (receiver + the A→B transfer); BlindOracle payments on Base mainnet.
- **x402.** The `x402` Python SDK signs EIP-3009 `TransferWithAuthorization`s; the facilitator settles
  gaslessly. 42 real payments across 4 SKUs during the build ($4.02), every one a USDC transfer on Base mainnet listed in `bo_calls.jsonl`.
- **Python.** `bo_client.py` (client + the reserve-never-leaves-the-parties rule), `scripts/demo.py`
  (the whole protocol in one command with hash-chained, HMAC-signed evidence), `scripts/skucheck.py`.
- **AI assistance.** Built with Claude Code from this repository's own SPEC.md, after kick-off, from a
  minimal CLAUDE.md that forbids reading anything outside the repo. See DISCLOSURE.md.

## Links
- Repo: https://github.com/craigmbrown/ethonline-sealed-bid
- Evidence (verbatim simulator output, tx hashes, contract reads): `EVIDENCE.md`
- Receivers on Base Sepolia: production `0xaDF984468f5C7DEeb82FA4c98f25CA3952921ce7`, simulation `0xA2eB7d6EEd6a4d0976cb29B226093E007E720590`
- Settlement tx: https://sepolia.basescan.org/tx/0x3d241f2b75f53b1e81761991f7e3b6160e2ea6d9a70c31eda2f588cad8521735
- A → B transfer: https://sepolia.basescan.org/tx/0x1389bfac88595dddb297c800dc1fafc9d160811466f36ad4de23a41340de9c49
- Video (108 s, captioned terminal recording of the real run): https://github.com/craigmbrown/ethonline-sealed-bid/releases/download/demo-2026-09-05/demo.mp4
  — release page https://github.com/craigmbrown/ethonline-sealed-bid/releases/tag/demo-2026-09-05; source cast `evidence/demo.cast`,
  regenerate with `scripts/render_cast.py`. A narrated screen recording per `docs/VIDEO-SCRIPT.md` can replace it before submission.

## Chainlink prize checklist (from the track page, read 2026-09-04)
| Requirement | Where |
|---|---|
| Confidential Workflow executes a meaningful part of the app | the sealing IS the app — `sealed-bid-ts/workflow.ts::runSealedBid` |
| Registers and uses a TEE handler (`handlerInTee`) | `sealed-bid-ts/workflow.ts::initWorkflow` |
| ≥1 sensitive input processed in-enclave | two reserves + two salts via `getSecrets`; the overlap is an in-enclave intermediate |
| Not a placeholder | 34 tests, negative controls, on-chain write on SETTLE |
| Successful execution: CRE CLI simulation or live deploy | verbatim simulator output for all three paths + broadcast writes to Base Sepolia (`EVIDENCE.md`) |
| Evidence in the submission | `EVIDENCE.md`, `evidence/`, video |
