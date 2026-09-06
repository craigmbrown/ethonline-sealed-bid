# Check-in — Sep 7 (ETHOnline 2026, Classic track)

Paste-ready. Written 2026-09-06; file by **Sep 7 23:59 ET**. Second check-in due Sep 10 23:59 ET.

## Where the project stands

Phase 1 (Sep 4–6) is complete and Phase 2 (Sep 7–10) is largely complete ahead of schedule.
The end-to-end protocol runs in one command and has executed for real.

| Task | State |
|---|---|
| 1 — public repo, disclosure, spec | done Sep 4 |
| 2 — CRE skeleton, `handlerInTee`, secrets read in-enclave | done Sep 4 |
| 3 — no-leak tests, written **before** the settlement logic | done Sep 4 |
| 4 — sealed-bid overlap + commitments | done Sep 4 |
| 6 — Base Sepolia receiver, settlement proven on chain | done Sep 5 |
| 5 — BlindOracle x402 client, real paid calls | done Sep 5 |
| 9 — one-command demo driver + recorded cast | done Sep 5 |
| 8 — README, architecture diagram, submission text | partial |
| 7 — dispute adjudication | built and gated, not exercised (no contested outcome) |

## Evidence a judge can check without us

- **Enclave**: `cre.handlerInTee` with a Nitro/us-west-2 constraint; both reserve prices read
  inside the handler in a single `getSecrets` call. Only `SETTLE @ <price>` / `NO_OVERLAP` /
  `INVALID_INPUT` crosses the boundary. Simulator output captured verbatim in `EVIDENCE.md`.
- **Settlement on chain** (Base Sepolia): `SETTLE @ 105` → tx `0x6a02d0ca…34c9`, block 46425501,
  `settlementCount()=1`, `getSettlement(runId)` returns `(105000000, cA, cB, ts)`.
  A `NO_OVERLAP` run sends **no transaction at all** — verified by owner nonce.
- **No-leak is structural, not a claim**: two deliberately leaky seals run through the identical
  output path as permanent negative controls. The suite cannot pass unless the checkers also
  catch a leak.
- **Third-party service calls are paid, not simulated**: 42 x402 calls, **$4.02**, USDC on Base
  mainnet, each verifiable with `cast receipt` (`bo_calls.jsonl` carries every tx hash).
- **Process attestation**: `conformant` + `attributable` under ed25519, tx verified on Basescan.

## Two things we are disclosing rather than hiding

1. **The payer wallet classifies as `payer_class: self` on the provider's public proof rail.**
   These are real payments for real deliverables, used exactly as an outside agent would — but
   they are **not** evidence of third-party demand, and we do not present them as such.
2. **`cre workflow simulate --broadcast` does not go through the CRE Forwarder.** It submits via
   a mock forwarder with a placeholder workflow owner, so a receiver pinned to the real Forwarder
   correctly reverts `UnauthorizedSender` — and the forwarder records `result=false` while the
   transaction itself succeeds. **`txStatus=SUCCESS` is not proof the receiver ran; read the
   receiver's state back.** We deployed a second, simulation-only receiver rather than weakening
   the production one. Measured, not assumed.

## Sep 7 → Sep 13

1. Re-read the Chainlink track page (the $500 Automated Liquidation Protection challenge read
   "Coming soon" on Sep 4 — pursue only if it costs nothing beyond work already done).
2. Finish Task 8 polish; demo video from `docs/VIDEO-SCRIPT.md`.
3. Optional live `cre workflow deploy` — the registry is on Ethereum mainnet and the org is at
   its 3-workflow quota, so this spends mainnet gas and requires deleting a template first.
   Simulation is explicitly accepted by the track qualifications, so this stays optional.
4. Submit by **Sep 13 16:00 UTC**.

Commits: 6 on Sep 4, 16 on Sep 5, 3 on Sep 6 — every event day, no single-dump history.
