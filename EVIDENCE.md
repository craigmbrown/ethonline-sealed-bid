# Evidence

Verbatim terminal output from `cre workflow simulate` (CRE CLI v1.31.0), per the Chainlink
"Best Confidential Workflow" qualification: *"Provide evidence of the successful simulation or
deployment in the submission, such as a demo video, terminal output, execution logs, or
deployment details."* Each run below is one execution of the `handlerInTee` handler with the
reserve prices and salts supplied as Vault secrets. Salts used for these runs:
`salt-a-0f3c9e` / `salt-b-77d1a2` (throwaway values, only for reproducing the hashes).

The simulator prints, before every run:

```
╭────────────────────────────────────────────────────────────────────────────────────────────────────╮
│ Trigger requested TEE Execution your trigger will run in one of the following Tees:                │
│     - AWS Nitro in us-west-2                                                                       │
│ The simulator is not a real TEE, and is meant to debug.                                            │
│ Do not use it for sensitive information.                                                            │
│ During real execution, user logs for this trigger will not be visible, and will not leave the TEE. │
│ They are presented in the simulator for debugging only.                                            │
╰────────────────────────────────────────────────────────────────────────────────────────────────────╯
```

## 2026-09-04 — Task 4: sealing + attestation

### SETTLE — buyer max 120, seller min 90

```
$ RESERVE_PRICE_AGENT_A_STAGING=120 RESERVE_PRICE_AGENT_B_STAGING=90 \
  COMMITMENT_SALT_A_STAGING=salt-a-0f3c9e COMMITMENT_SALT_B_STAGING=salt-b-77d1a2 \
  cre workflow simulate ./sealed-bid-ts --target=staging-settings --non-interactive --trigger-index 0

2026-09-04T22:22:09Z [USER LOG] enclave: outcome=SETTLE runId=0xd15092f20ee2129949823e8ae59e1d0847f9cae369e343bfb3ad39d0fa73b804
2026-09-04T22:22:09Z [USER LOG] enclave: commitmentA=0x3f8d9f965b0ac8f16db167d6489f8732bedfe9cc40f551ecccab05fb23737319 commitmentB=0x73786c9229a0b79ed9b92ffa8eecc51b7f0f218294cb54996dc50e8ab04dbf0e
✓ Workflow Simulation Result:
"SETTLE @ 105 (run: staging)"
```

Neither `120` nor `90` appears in any output. `105` is the protocol midpoint.

### NO_OVERLAP — buyer max 90, seller min 120

```
$ RESERVE_PRICE_AGENT_A_STAGING=90 RESERVE_PRICE_AGENT_B_STAGING=120 \
  COMMITMENT_SALT_A_STAGING=salt-a-0f3c9e COMMITMENT_SALT_B_STAGING=salt-b-77d1a2 \
  cre workflow simulate ./sealed-bid-ts --target=staging-settings --non-interactive --trigger-index 0

2026-09-04T22:22:18Z [USER LOG] enclave: outcome=NO_OVERLAP runId=0x5e66097e42dbf1e73be8d6f55f260e46c8505a7eb51e3d3342e7aa16b542c192
2026-09-04T22:22:18Z [USER LOG] enclave: commitmentA=0x66370ae1042a0c38387e724cb607bc2400acc3f065e0fe518b4b74b4cd3a58ec commitmentB=0x7c8e861e4ad020b73632855f19169cacc990bb50c8ce293d58ca16187792fb6d
✓ Workflow Simulation Result:
"NO_OVERLAP (run: staging)"
```

No price, no gap, no ordering. Only the salted commitments differ from any other NO_OVERLAP run.

### INVALID_INPUT — buyer max 120, seller min `abc`

```
$ RESERVE_PRICE_AGENT_A_STAGING=120 RESERVE_PRICE_AGENT_B_STAGING=abc \
  COMMITMENT_SALT_A_STAGING=salt-a-0f3c9e COMMITMENT_SALT_B_STAGING=salt-b-77d1a2 \
  cre workflow simulate ./sealed-bid-ts --target=staging-settings --non-interactive --trigger-index 0

2026-09-04T22:22:27Z [USER LOG] enclave: outcome=INVALID_INPUT runId=0xd89c467ee1e07d958f7ed670e865138f0f46fa8384c6468b0fedaf8467a538ad
2026-09-04T22:22:27Z [USER LOG] enclave: commitmentA=0x0000000000000000000000000000000000000000000000000000000000000000 commitmentB=0x0000000000000000000000000000000000000000000000000000000000000000
✓ Workflow Simulation Result:
"INVALID_INPUT (run: staging)"
```

Nothing partial leaves: zero commitments, zero clearing price, and the malformed value is not echoed.

### Test suite at this commit

```
$ cd sealed-bid-ts && bun test
 27 pass
 0 fail
 81 expect() calls
```

Includes the negative controls (`leakySealReturnsReserve`, `leakySealRevealsGap`, an off-midpoint
SETTLE) that the no-leak checkers must catch on every run.
