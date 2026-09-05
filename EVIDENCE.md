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

## 2026-09-05 — Task 6: settlement on Base Sepolia

Chain: Base Sepolia (chain id 84532). Workflow owner / deployer: `0x2D0B6cd9485e59a6eDc10B048227FAF0e81D174D`.
Contract: `contracts/src/SealedBidReceiver.sol` (13 forge tests, incl. a fuzz). Deployed twice, on purpose —
see the measured finding below.

| Receiver | Address | Trusts forwarder | Pinned workflow owner | Purpose |
|---|---|---|---|---|
| production | [`0xaDF984468f5C7DEeb82FA4c98f25CA3952921ce7`](https://sepolia.basescan.org/address/0xaDF984468f5C7DEeb82FA4c98f25CA3952921ce7) | `0xF8344CFd5c43616a4366C34E3EEE75af79a74482` (the CRE Forwarder from `cre workflow supported-chains`) | `0x2D0B…174D` | a live `cre workflow deploy` writes here |
| simulation | [`0xA2eB7d6EEd6a4d0976cb29B226093E007E720590`](https://sepolia.basescan.org/address/0xA2eB7d6EEd6a4d0976cb29B226093E007E720590) | `0x82300bd7c3958625581cc2F77bC6464dcEcDF3e5` (the simulator's mock forwarder) | `0xaAaA…aAaA` (the simulator's placeholder owner) | `cre workflow simulate --broadcast` writes here |

Deploy transactions: production `0x523df443af4a0e6a61ef797c808c4879781dcd00a86c49e83e49ba0d625cc9c8` (block 46425337,
506,483 gas); simulation `0xb79dcca4e6bbf164611f8cd2c2b1b0f325f07e611001a741503e5c09352d925a` (block 46425488).

### Measured: the simulator does not sign as the production DON — and the receiver refused it

First broadcast targeted the **production** receiver (`--target=staging-settings --broadcast`):

```
2026-09-05T14:38:57Z [USER LOG] enclave: outcome=SETTLE runId=0xd15092f20ee2129949823e8ae59e1d0847f9cae369e343bfb3ad39d0fa73b804
2026-09-05T14:38:59Z [USER LOG] settlement: written to 0xaDF984468f5C7DEeb82FA4c98f25CA3952921ce7 tx=0x61c816977f3ec86c13fbe0e07a0500eb5bb9ff1984f7911518a5e2d3d86a67fc
✓ Workflow Simulation Result:
"SETTLE @ 105 (run: staging) tx: 0x61c816977f3ec86c13fbe0e07a0500eb5bb9ff1984f7911518a5e2d3d86a67fc"
```

The transaction succeeded (`status 0x1`, block 46425426) but `settlementCount()` stayed **0**. Decoding the
calldata (`report(address,bytes,bytes,bytes[])`): the tx went **to `0x82300bd7…dF3e5`, not to the CRE
Forwarder**, and the 109-byte report header carried `workflowOwner = 0xaaaa…aaaa`, `workflowId = 0x1111…1111`.
The mock forwarder's `ReportProcessed` event recorded `result = false`: the production receiver reverted
`UnauthorizedSender` because `msg.sender` was not the Forwarder it was built with. That is the contract doing
its job — a simulator-signed report must not be able to settle the production instance. The simulation
receiver above exists so the simulator's own signer has something it *is* authorised to write to.

### SETTLE — buyer max 120, seller min 90, broadcast to the simulation receiver

```
$ RESERVE_PRICE_AGENT_A_STAGING=120 RESERVE_PRICE_AGENT_B_STAGING=90 \
  COMMITMENT_SALT_A_STAGING=salt-a-0f3c9e COMMITMENT_SALT_B_STAGING=salt-b-77d1a2 \
  cre workflow simulate ./sealed-bid-ts --target=simulation-settings --non-interactive --trigger-index 0 --broadcast -e ./.env

2026-09-05T14:41:27Z [USER LOG] enclave: outcome=SETTLE runId=0xc5c693bb8edefd9653add5c1627425fa472221b74daacfdaf59d814aba415b00
2026-09-05T14:41:27Z [USER LOG] enclave: commitmentA=0x3f8d9f965b0ac8f16db167d6489f8732bedfe9cc40f551ecccab05fb23737319 commitmentB=0x73786c9229a0b79ed9b92ffa8eecc51b7f0f218294cb54996dc50e8ab04dbf0e
2026-09-05T14:41:29Z [USER LOG] settlement: written to 0xA2eB7d6EEd6a4d0976cb29B226093E007E720590 tx=0x6a02d0ca2ab8bee53f2745047ffdfce8c1ee45f0cac35d1bd7d37a9196c834c9
✓ Workflow Simulation Result:
"SETTLE @ 105 (run: simulation) tx: 0x6a02d0ca2ab8bee53f2745047ffdfce8c1ee45f0cac35d1bd7d37a9196c834c9"
```

On-chain state read back with `cast` (block 46425501, 181,480 gas, tx
[`0x6a02d0ca…34c9`](https://sepolia.basescan.org/tx/0x6a02d0ca2ab8bee53f2745047ffdfce8c1ee45f0cac35d1bd7d37a9196c834c9)):

```
$ cast call 0xA2eB7d6EEd6a4d0976cb29B226093E007E720590 "settlementCount()(uint256)" --rpc-url https://sepolia.base.org
1
$ cast call 0xA2eB7d6EEd6a4d0976cb29B226093E007E720590 "getSettlement(bytes32)((uint256,bytes32,bytes32,uint64))" \
    0xc5c693bb8edefd9653add5c1627425fa472221b74daacfdaf59d814aba415b00 --rpc-url https://sepolia.base.org
(105000000, 0x3f8d9f965b0ac8f16db167d6489f8732bedfe9cc40f551ecccab05fb23737319, 0x73786c9229a0b79ed9b92ffa8eecc51b7f0f218294cb54996dc50e8ab04dbf0e, 1788619290)
```

The receipt carries the receiver's own `Settled(bytes32,uint256,bytes32,bytes32,string)` event (topic0
`0xcb097252…`) followed by the forwarder's `ReportProcessed`. What is on chain: the clearing price
105.000000, the two salted commitments, the run id, a block timestamp. Neither 120 nor 90 exists anywhere in
the transaction.

### NO_OVERLAP — buyer max 90, seller min 120, same broadcast flags

```
2026-09-05T14:41:37Z [USER LOG] enclave: outcome=NO_OVERLAP runId=0x84d1238f8f506e4bbcf10a66ec94c9f432db81a33140587dd7cb9665e5e8f76b
2026-09-05T14:41:37Z [USER LOG] enclave: commitmentA=0x66370ae1042a0c38387e724cb607bc2400acc3f065e0fe518b4b74b4cd3a58ec commitmentB=0x7c8e861e4ad020b73632855f19169cacc990bb50c8ce293d58ca16187792fb6d
✓ Workflow Simulation Result:
"NO_OVERLAP (run: simulation)"
```

No `settlement:` log line, no tx hash. `getSettlement(0x84d1238f…f76b)` returns all zeros, `settlementCount()`
is still 1, and the owner's nonce is 4 = two deploys + two SETTLE broadcasts. **A NO_OVERLAP run leaves no
transaction at all**, and even if one were forced, the receiver reverts `NotASettlement` (forge test).

### Test suites at this commit

```
$ cd contracts && forge test
Suite result: ok. 13 passed; 0 failed; 0 skipped
$ cd sealed-bid-ts && bun test
 34 pass
 0 fail
 123 expect() calls
```

## 2026-09-05 — Task 5: real paid BlindOracle calls (x402, USDC on Base mainnet)

`bo_client.py` answers each HTTP 402 with an EIP-3009 `TransferWithAuthorization` signed by the
payer wallet (`0x2D0B6cd9485e59a6eDc10B048227FAF0e81D174D`, also the CRE workflow owner); the
facilitator submits the transfer, so the payer spends no gas. Prices are read from the live
challenge, never hardcoded. **Nine calls, three distinct SKUs, $0.57 total**, each a USDC
transfer on Base mainnet (chain id 8453) to the treasury named in the challenge
(`0x5E709929A4AB69eC3a8811d03417869059BC4EB9`). The full ledger is `bo_calls.jsonl`; the
deliverables of the second bracket are committed under `evidence/bo/`.

### Start-of-session smoke test (free)

```
$ python3 scripts/skucheck.py
skucheck against https://api.craigmbrown.com
  ✓ reputation.lookup                    $0.01
  ✓ agent.trust-badge                    $0.01
  ✓ agent.prehire-check                  $0.25
  ✓ security.process-attestation         $0.25
  ✓ arbitration.dispute-settlement       $5.0
  · dispute disclosure: adjudicator=blindoracle_operator_panel neutrality=unilateral
  ✓ 402 challenge: exact, Base mainnet, amount=10000 (micro-USDC), payTo=0x5E709929A4AB69eC3a8811d03417869059BC4EB9, asset=USDC
  ✓ proof rail up: 945 settlements scanned, latest kind 30120
  ✓ payer 0x2D0B6cd9485e59a6eDc10B048227FAF0e81D174D holds 10.82 USDC on Base; one bracket costs $0.52
GREEN — the paid path is live
```

### The bracket around the SETTLE run (`runId 0xc5c693bb…5b00`), second execution — deliverables in `evidence/bo/`

| Step | SKU | Price | Deliverable (abridged) | Base mainnet tx |
|---|---|---|---|---|
| pre-bid vet of the seller | `reputation.lookup` `sealedbid-agent-b` | $0.01 | `found:true score:0 badge:none` — "registered passport with NO provider track record yet — an honest zero, not a rating" | [`0x88d7905a…3cac`](https://basescan.org/tx/0x88d7905a296c5d5c96bd6f06ee272b128a09f6e6f46c48f89771ad8169393cac) |
| bracket open | `agent.trust-badge` `sealedbid-agent-a`, `task_id = runId` | $0.01 | `badge_label:UNVERIFIED issued:true` — "identity verification only — NOT code quality or capability" | [`0xbf21bf34…f3be`](https://basescan.org/tx/0xbf21bf34e58fb278eb9df35439652a01491a0c829783943443237fd670cdf3be) |
| high-value screen of the seller | `agent.prehire-check` `sealedbid-agent-b` | $0.25 | `verdict:CAUTION`, `red_flags:[not_audited, no_delivery_history, no_local_history]`, `method: deterministic ledger lookup — zero LLM` | [`0x5cfc2e9a…2372`](https://basescan.org/tx/0x5cfc2e9aed1ae8a13594fe26f88b7d4e32cab1e2ca40127ed2adc3f483c32372) |
| bracket close | `reputation.lookup` `sealedbid-agent-b` | $0.01 | `found:true score:0 badge:none` | [`0xefcf4a5c…ce63`](https://basescan.org/tx/0xefcf4a5c83279c5e8dcf708a70622eb28dba08af75c35dbae71b358ab308ce63) |

`changed_outcome=false` on every vet: a zero-history counterparty on a $105 trade is below the
pre-hire threshold in the demo, so the CAUTION verdict is recorded and the run proceeds. That is
the honest reading of a brand-new passport, and it is exactly what the SKU says it is.

First bracket (same four steps, 18:45Z): txs `0x0f267d3c…922d`, `0x5e636ec4…e78`, `0x742581fd…02de`,
`0x6661ae6f…9b5d`. First-ever call (18:44Z, before the parser read the `payment` block):
`0x7476a5f7…dcdb`.

### Verified from the chain, not from the API

```
$ cast receipt 0x7476a5f70942b33a90ab8f9a57588d0c61083de261601474ddcbdf58d4e6dcdb --rpc-url https://mainnet.base.org
status 0x1 block 50922254 submitted by 0xe74817f4cdc15844314812b2271276e64e890fae (facilitator)
USDC Transfer from 0x2d0b…174d to 0x5e70…4eb9 amount 10000 micro-USDC     ← $0.01, gasless for the payer

$ cast receipt 0x5cfc2e9aed1ae8a13594fe26f88b7d4e32cab1e2ca40127ed2adc3f483c32372 --rpc-url https://mainnet.base.org
status 0x1 block 50922324 submitted by 0x68a96f41ff1e9f2e7b591a931a4ad224e7c07863 (facilitator)
USDC Transfer from 0x2d0b…174d to 0x5e70…4eb9 amount 250000 micro-USDC    ← $0.25

$ cast call USDC "balanceOf(address)" 0x2D0B…174D   # before → after the nine calls
10816202 → 10246202 micro-USDC                     ← exactly $0.57 spent
```

### What the public proof rail says about these payments — recorded, not hidden

`GET /v1/proofs/settlement/0x7476a5f7…dcdb` returns a `LedgerBackedSettlementReceipt` with
`rail: usdc_base`, `amount_usd: 0.01`, `settlement_ref_resolved: true` — **and
`payer_class: "self"`**: "The payer is a wallet the fleet operates (self-pay or dogfood). Such
settlements are real on-chain transfers but are deliberately NOT issued a reputation-bearing
ProofOfSettledOutcome." The payer wallet is the author's, and the author operates BlindOracle
(see `DISCLOSURE.md` §1). So: the payments are real, the deliverables are real, the API was used
exactly as any outside agent would use it — and these nine calls are **not** evidence of
third-party demand for BlindOracle, and the rail itself says so.

### Two demo passports (free, `POST /v1/agents/register`)

| Agent | agent_id | tier | wallet |
|---|---|---|---|
| `sealedbid-agent-a` (buyer, pays) | `agent_69cdcf6a04fa` | observer | `0x2D0B…174D` |
| `sealedbid-agent-b` (seller) | `agent_295d689e1f02` | observer | `0xaE4B357dBf9127b17050a0128Dc146fa31bE89c1` (fresh, unfunded) |

### Test suite

```
$ python3 -m pytest tests/test_bo_client.py -q
11 passed
```

Includes the evidence-rule tests: `build_dispute_evidence()` never contains a reserve,
`assert_no_reserve()` catches a reserve in a string, a number, or a nested field (hex hashes
excluded), and `dispute()` refuses a leaking payload before anything is sent.
