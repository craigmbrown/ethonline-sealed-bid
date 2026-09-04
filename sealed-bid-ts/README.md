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
| `workflow.ts` | config schema, `parseReserve`, `sealBids` (§2.1), `commit` / `runIdFor` (§2.2), `runSealedBid` (the TEE handler), `initWorkflow` |
| `noleak.ts` | the no-leak checkers used by the tests: `assertNoReserveLeak`, `assertIdenticalRuns`, `decodeReports`, `numericTokens` |
| `workflow.test.ts` | 27 tests: sealing cases, commitments, plumbing, and the load-bearing no-leak tests with leaky seals as negative controls |

## State

- Task 2 ✅ skeleton · Task 3 ✅ no-leak tests · Task 4 ✅ sealing + attestation.
- Task 6 ⏳ on-chain settlement receiver on Base Sepolia (the report is already EVM-encoded for it).

```bash
bun install
bun run typecheck
bun test

# from the repo root; the simulator resolves secret values from the env-var
# names in ../secrets.yaml (export them, or put them in a gitignored .env):
RESERVE_PRICE_AGENT_A_STAGING=120 RESERVE_PRICE_AGENT_B_STAGING=90 \
COMMITMENT_SALT_A_STAGING=salt-a-0f3c9e COMMITMENT_SALT_B_STAGING=salt-b-77d1a2 \
  cre workflow simulate ./sealed-bid-ts --target=staging-settings --non-interactive --trigger-index 0
# → "SETTLE @ 105 (run: staging)"   (swap the two prices for NO_OVERLAP)
```

See `../EVIDENCE.md` for captured runs. Do not run `cre update`; the project is pinned to CRE CLI
v1.31.0 and `@chainlink/cre-sdk` 1.18.0.
