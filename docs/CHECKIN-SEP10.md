# Check-in — Sep 10 (ETHOnline 2026, Classic track)

Paste-ready. Due **Sep 10 23:59 ET**; this text was written 2026-09-11 UTC, after the
deadline, and says so rather than back-dating. First check-in: `docs/CHECKIN-SEP7.md`.

## Where the project stands

The minimum submission was complete on Sep 6. No code changed between Sep 7 and Sep 10;
Sep 11 is submission preparation only (documentation brought up to the ledger, nothing new
built). Every layer has executed for real and is re-verified today.

| Task | State |
|---|---|
| 1 — public repo, disclosure, spec | done Sep 4 |
| 2 — CRE skeleton, `handlerInTee`, secrets read in-enclave | done Sep 4 |
| 3 — no-leak tests, written **before** the settlement logic | done Sep 4 |
| 4 — sealed-bid overlap + commitments | done Sep 4 |
| 6 — Base Sepolia receiver, settlement proven on chain | done Sep 5 |
| 5 — BlindOracle x402 client, real paid calls | done Sep 5 |
| 9 — one-command demo driver + recorded cast + MP4 release | done Sep 5 |
| 8 — README, architecture diagram, submission text, attestation | done Sep 5–6 |
| 7 — dispute adjudication | built and gated, not exercised (no contested outcome) |

## Re-verified 2026-09-11

- `bun test` 34 pass · `forge test` 13 pass · `pytest` 23 pass.
- 42 paid x402 calls, $4.02, 4 SKUs, USDC on Base mainnet — one tx hash per row in `bo_calls.jsonl`.
- Process attestation: `conformant`, `signature_binding: attributable` (ed25519), tx on Basescan.
- No private-repo reference and no secret shape in the tree (the two greps in `EVIDENCE.md`).

## What we are still disclosing

1. The payer wallet is classified `payer_class: self` on the provider's proof rail — real
   payments, not evidence of third-party demand.
2. `cre workflow simulate --broadcast` signs through the simulator's mock forwarder, so the
   settlement evidence is on a simulation-only receiver; the production receiver correctly
   rejects it. Both receivers are on Base Sepolia and both are documented.
3. Commit cadence: 6 / 16 / 4 commits on Sep 4 / 5 / 6, then none until Sep 11. The history
   is still incremental and unsquashed; it is not continuous.

## Sep 11 → Sep 13

1. Submission text is final in `docs/SUBMISSION.md`; submit before **Sep 13 16:00 UTC**.
2. Optional: narrated screen recording per `docs/VIDEO-SCRIPT.md` to replace the terminal MP4.
3. Optional and operator-only: live `cre workflow deploy` to the production receiver (mainnet
   registry gas). Simulation is explicitly accepted by the track qualifications.
