# sealed-bid-ts — the CRE Confidential Workflow

The sealing runs inside a `cre.handlerInTee` handler (`workflow.ts`). Both reserve prices and
both commitment salts are Vault secrets read with **one** `getSecrets` call inside the enclave.
The enclave computes the outcome and the salted input commitments, and only the attestation
record crosses back to the DON via `usingTheDons().report(...)`:

```
(string result, uint256 clearingPriceMicro, string runLabel,
 bytes32 commitmentA, bytes32 commitmentB, bytes32 runId)
```

| Outcome | What leaves the enclave |
|---|---|
| `SETTLE` | `SETTLE @ <midpoint>`, clearing price in micro-units, both commitments, runId |
| `NO_OVERLAP` | the literal `NO_OVERLAP`, zero price, both commitments, runId — nothing about gap or order |
| `INVALID_INPUT` | the literal `INVALID_INPUT`, zeros everywhere — nothing partial |

## Files

| File | Role |
|---|---|
| `workflow.ts` | config schema, `parseReserve`, `sealBids` (§2.1), `commit` / `runIdFor` (§2.2), `writeSettlement` (§2.3), `runSealedBid` (the TEE handler), `initWorkflow` |
| `noleak.ts` | the no-leak checkers used by the tests: `assertNoReserveLeak`, `assertIdenticalRuns`, `decodeReports`, `numericTokens` |
| `workflow.test.ts` | 34 tests: sealing cases, commitments, plumbing, the settlement-write rule (exactly one write on SETTLE, none otherwise), and the load-bearing no-leak tests with leaky seals as negative controls |
| `config.staging.json` / `config.production.json` | point at the **production** receiver (`0xaDF9…1ce7`, trusts the CRE Forwarder) — used by a live `cre workflow deploy` |
| `config.simulation.json` | points at the **simulation** receiver (`0xA2eB…0590`, trusts the simulator's mock forwarder) — used by `cre workflow simulate --broadcast` |

## Settlement (SPEC §2.3)

After the enclave reports, the DON side calls `EVMClient.writeReport` **only when the result is SETTLE**
and `receiverAddress` is not the zero address. The Forwarder verifies the DON signatures and calls
`SealedBidReceiver.onReport`, which stores the clearing price and both commitments under the run id
(`../contracts/`). `NO_OVERLAP` and `INVALID_INPUT` never reach the write; the receiver independently
reverts anything that is not a SETTLE. Set `receiverAddress` to `0x000…000` for a pure off-chain simulation.

`cre workflow simulate --broadcast` signs with the simulator's own mock forwarder and placeholder owner, not
with the production DON — so the production receiver correctly rejects it. That is why there are two
receivers; `../EVIDENCE.md` records the measurement.

## State

- Task 2 ✅ skeleton · Task 3 ✅ no-leak tests · Task 4 ✅ sealing + attestation · Task 6 ✅ settlement on Base Sepolia.

```bash
bun install
bun run typecheck
bun test

# from the repo root; the simulator resolves secret values from the env-var
# names in ../secrets.yaml (export them, or put them in a gitignored .env):
RESERVE_PRICE_AGENT_A_STAGING=120 RESERVE_PRICE_AGENT_B_STAGING=90 \
COMMITMENT_SALT_A_STAGING=salt-a-0f3c9e COMMITMENT_SALT_B_STAGING=salt-b-77d1a2 \
  cre workflow simulate ./sealed-bid-ts --target=staging-settings --non-interactive --trigger-index 0
# → "SETTLE @ 105 (run: staging) tx: 0x000…000"   (dry-run write; swap the two prices for NO_OVERLAP)

# real on-chain write through the simulator's mock forwarder (needs CRE_ETH_PRIVATE_KEY in ../.env,
# funded with a little Base Sepolia ETH):
RESERVE_PRICE_AGENT_A_STAGING=120 RESERVE_PRICE_AGENT_B_STAGING=90 \
COMMITMENT_SALT_A_STAGING=salt-a-0f3c9e COMMITMENT_SALT_B_STAGING=salt-b-77d1a2 \
  cre workflow simulate ./sealed-bid-ts --target=simulation-settings --non-interactive --trigger-index 0 --broadcast -e ./.env
# → "SETTLE @ 105 (run: simulation) tx: 0x6a02…"   then:
cast call 0xA2eB7d6EEd6a4d0976cb29B226093E007E720590 "settlementCount()(uint256)" --rpc-url https://sepolia.base.org
```

See `../EVIDENCE.md` for captured runs. Do not run `cre update`; the project is pinned to CRE CLI
v1.31.0 and `@chainlink/cre-sdk` 1.18.0.
