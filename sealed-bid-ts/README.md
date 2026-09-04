# sealed-bid-ts — the CRE Confidential Workflow

The sealing runs inside a `cre.handlerInTee` handler (`workflow.ts`). Both reserve prices are
Vault secrets read with **one** `getSecrets` call inside the enclave; only an `EnclaveOutcome`
(`SETTLE @ price` / `NO_OVERLAP` / `INVALID_INPUT`) crosses back to the DON via
`usingTheDons().report(...)`.

## Files

| File | Role |
|---|---|
| `workflow.ts` | config schema, `parseReserve`, `sealBids` (the sealing function), `runSealedBid` (the TEE handler), `initWorkflow` |
| `noleak.ts` | the no-leak checkers used by the tests: `assertNoReserveLeak`, `assertIdenticalRuns`, `decodeReports` |
| `workflow.test.ts` | unit tests + the load-bearing no-leak tests, with two deliberately leaky seals as negative controls |

## State

- Task 2 ✅ skeleton: inputs read and validated in the TEE, outcome crosses to the DON.
- Task 3 ✅ no-leak tests: (a) no reserve in any emitted channel; (b) `NO_OVERLAP` is
  byte-identical for near / far / reversed bands. Both are proven to *fail* against leaky
  seals on every run (`NEGATIVE CONTROL` tests).
- Task 4 ⏳ the band-overlap maths (SPEC §2.1) replaces one marked line in `sealBids`.

```bash
bun install
bun run typecheck
bun test

# from the repo root; the simulator resolves secret values from the env-var
# names in ../secrets.yaml (export them, or put them in a gitignored .env):
RESERVE_PRICE_AGENT_A_STAGING=120 RESERVE_PRICE_AGENT_B_STAGING=90 \
  cre workflow simulate ./sealed-bid-ts --target=staging-settings --non-interactive --trigger-index 0
```

Do not run `cre update`; the project is pinned to CRE CLI v1.31.0 and `@chainlink/cre-sdk` 1.18.0.
