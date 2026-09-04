# sealed-bid-ts — the CRE Confidential Workflow

The sealing runs inside a `cre.handlerInTee` handler (`workflow.ts`). Both reserve prices are
Vault secrets read with **one** `getSecrets` call inside the enclave; only the outcome crosses
back to the DON via `usingTheDons().report(...)`.

Task 2 state: skeleton — reads and validates both inputs in the TEE, returns
`SEALED_INPUTS_OK` / `INVALID_INPUT`. The band-overlap maths (SPEC §2.1) lands in Task 4,
after the no-leak tests (Task 3).

```bash
bun install
bun run typecheck
bun test

# from the repo root; the simulator resolves secret values from the env var
# names in ../secrets.yaml (export them, or put them in a gitignored .env):
RESERVE_PRICE_AGENT_A_STAGING=120 RESERVE_PRICE_AGENT_B_STAGING=90 \
  cre workflow simulate ./sealed-bid-ts --target=staging-settings --non-interactive --trigger-index 0
```

Do not run `cre update`; the project is pinned to CRE CLI v1.31.0 and `@chainlink/cre-sdk` 1.18.0.
