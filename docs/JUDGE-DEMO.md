# Judge demo — BO-Sealed-Bid (ETHOnline 2026)

Run-of-show for "show us what you built". Five minutes of demo, then questions. Every claim
below points at something a judge can check without trusting us: a transaction, a contract
read, or a test they can run.

Showcase: https://ethglobal.com/showcase/bo-sealed-bid-79v4s · Repo: https://github.com/craigmbrown/ethonline-sealed-bid

---

## The one sentence

**Two agents can agree a price without either revealing its limit, and without a middleman
who sees both — the numbers are sealed in a Chainlink CRE enclave, and the only thing that
leaves it is `SETTLE @ midpoint` or `NO_OVERLAP`.**

## Why a judge should care (the value, before any code)

| Today's agent commerce | With this |
|---|---|
| Reveal your reserve → you get priced at the edge of it | Reserve never leaves the enclave; the counterparty learns only the clearing price |
| Or trust a matching service that sees both numbers | No service sees both. The DON computes the overlap inside a TEE and signs only the result |
| A failed negotiation still leaks ("they wanted more than X") | `NO_OVERLAP` is byte-identical whether the gap is 1 or 1,000. Nothing is written on chain |
| Settlement is self-reported | Settlement is a DON-signed report accepted by a contract that trusts only the CRE Forwarder |
| Counterparty trust is a vibe | Each agent vets the other through a paid, third-party API before bidding, and buys an attestation over the run afterwards |

Cost of a full run with vetting: about **$0.79** in USDC plus Base Sepolia gas. The sealing
itself costs nothing beyond the workflow execution.

---

## Run-of-show (5:00)

Layout: terminal left, Basescan tab right. Have `EVIDENCE.md` open in a third tab as the fallback.

### 0:00–0:40 — The problem, in one breath

Say it, don't slide it: *"When two agents negotiate, one has to go first. Whoever reveals a
reserve loses. The usual fix is a trusted middleman — which is the thing agents are supposed
to remove. We put the middleman in an enclave and let it say exactly one word."*

Show: the README diagram (top of the repo page). Point at the enclave box and the two outputs.

### 0:40–1:40 — Prove it happened for real, before showing any code

Open Basescan on the **live DON settlement**:
https://sepolia.basescan.org/tx/0x7baefa9ebb15dfd3c9ad4c488e3e2ce558096020f19bb7ef352187c21ce2b2e9

Point at three things, in this order:

1. `To:` is `0xF8344CFd…4482` — **the CRE Forwarder**, not our wallet, not a script. The
   production DON produced this report.
2. The `Settled` event on `0xaDF9…1ce7` — our receiver, which only accepts reports from that
   Forwarder and only from the pinned workflow owner.
3. Then read the receiver live (read-only, safe, ~1 s):

```bash
cast call 0xaDF984468f5C7DEeb82FA4c98f25CA3952921ce7 \
  "getSettlement(bytes32)(uint256,bytes32,bytes32,uint256)" \
  0x162422a54440cb544a415e08c63ac0b7a5bbbdf729f7b20f8ef598859cad35bf \
  --rpc-url https://sepolia.base.org
```

*"105 — the midpoint of 120 and 90. Two commitments. A timestamp. That is everything the chain
knows. Neither 120 nor 90 is anywhere."*

### 1:40–2:40 — The other case, live and free

```bash
python3 scripts/demo.py --buyer-max 90 --seller-min 120 --broadcast --no-pay
```

(~20 s. Simulator run, no payment, no chain write.) Point at the two lines:

```
enclave says: 'NO_OVERLAP (run: simulation)'
abort   NO_OVERLAP: nothing written on chain, nothing transferred
```

Then: `cast call … "settlementCount()(uint256)"` on the receiver — unchanged.

*"Not the gap. Not which side was higher. No transaction. And the contract itself reverts
anything that is not a SETTLE, so even a misbehaving workflow could not leave a trace."*

### 2:40–3:20 — The security property is a test, not a promise

```bash
cd sealed-bid-ts && bun test
```

(~0.5 s, 34 pass.) Open `workflow.test.ts` and scroll to the two **leaky seals** — one returns
a reserve, one leaks the gap. *"They run through the identical output path every time. The
suite passes only if the checkers catch both. If a leaky seal ever passes, the checkers are
broken."*

### 3:20–4:10 — What CRE specifically made possible (the technical core)

Open `sealed-bid-ts/workflow.ts` at the three lines that matter:

| Line | What | Why it matters |
|---|---|---|
| `cre.handlerInTee(...)` (L241) | the handler is registered for the TEE, not the ordinary DON | the reserves are decrypted **only** inside AWS Nitro |
| `.getSecrets([...])` (L165) | one call, four Vault secrets | the parties' numbers are Vault secrets, never config, never args |
| `.writeReport(don, …)` (L132) | on SETTLE only | the DON signs; the Forwarder delivers; the contract verifies the sender |

Then the honest engineering line, which judges respect more than a polished claim: *"Three
things we had to measure rather than read: only the first `getSecrets` call per execution
works; nine ids per call max; and `simulate --broadcast` signs through a mock forwarder, so a
receiver built for the real one rejects it. That last one is why there are two receivers, and
why the live deploy mattered — it is the only evidence the simulator cannot fake."*

### 4:10–4:40 — Settlement is not self-certified

Scroll `EVIDENCE.md` to the Task 5 table: four paid calls around one run, each a USDC transfer
on Base mainnet with a Basescan link. *"Vetting before, attestation after, all paid over x402,
all verifiable. And the attestation went `non_conformant` → `conformant` → `attributable`
under ed25519 — recorded in that order, because the first result was real."*

### 4:40–5:00 — Where it goes

*"This deployment settles one fixed pair, every tick. Per-trade secrets or an HTTP trigger
makes it one workflow per negotiation. The dispute path is built and gated — a neutral
adjudicator gets the attestation and a hash, never the reserve. That is the piece that makes
agent-to-agent price discovery something you can actually run without a company in the
middle."*

---

## The 60-second version (if time is short)

1. Basescan tab: `To:` = CRE Forwarder, `Settled` event, price 105. *"The DON did this, not us."*
2. `cast call getSettlement` — *"price, two commitments, nothing else."*
3. `bun test` — *"34 pass, including two deliberately leaky seals that must be caught."*
4. *"NO_OVERLAP writes nothing. Sealed-bid without a middleman. Chainlink CRE, Base, x402."*

---

## Likely questions — and the honest answers

| Question | Answer |
|---|---|
| **Doesn't SETTLE at the midpoint reveal something?** | Yes: `A_max + B_min`. That is the standard sealed-bid trade-off; it is stated in the README, not hidden. Each side learns the sum, not the other's number. |
| **Why is the price 105 every time?** | This deployment's reserves are fixed Vault values (120 / 90) so every cron tick is the same run — the receiver refused the second tick as `RunAlreadySettled`, which we kept as evidence. Per-trade secrets or an HTTP trigger is the production shape. |
| **Why two receivers on Base Sepolia?** | `cre workflow simulate --broadcast` signs through the simulator's mock forwarder with a placeholder owner. The production receiver, pinned to the real Forwarder and owner, correctly rejected it. We deployed a second, simulation-only receiver rather than weaken the real one. Both are documented. |
| **Are the BlindOracle payments third-party demand?** | No, and we say so. The payer wallet is one the BlindOracle operator also uses; the proof rail marks them `payer_class: self`. They are real USDC transfers for real deliverables, used exactly as an outside agent would — not evidence of adoption. |
| **What does the ed25519 attestation prove?** | That the evidence bundle was produced by the holder of the published key and was not altered. It does not prove who the keyholder is (the key is self-published), and it does not verify the log's content. The on-chain settlement is the load-bearing evidence. |
| **Is the enclave output verified on chain?** | The Forwarder verifies the DON's signatures over the report; the receiver verifies the Forwarder and the workflow owner. What is not verified on chain is that the enclave *code* was the code in this repo — that is the CRE trust model. |
| **What was AI-written?** | Code, tests and docs were produced with Claude Code from this repo's own `SPEC.md` after kick-off, with a `CLAUDE.md` forbidding reads outside the repo. A private predecessor exists and was never opened. `DISCLOSURE.md` states all of it. |
| **Could a malicious workflow leak a reserve?** | Through the report — no: the receiver ABI-decodes a fixed shape and records only SETTLE; and the no-leak tests check the report's string and uint fields. Through logs — the TEE handler's logs are the only place, and the tests assert no reserve appears in them. |
| **Why Base Sepolia and not mainnet for settlement?** | Testnet for the settlement rail during the event; the vetting payments are already on Base mainnet. Nothing in the contract is testnet-specific. |
| **What did CRE cost?** | Four mainnet registry txs (delete slot, secrets allowlist, deploy, pause) under 0.0001 ETH total. Execution is on the DON. |

---

## Pre-demo checklist (10 minutes before)

```bash
cd ~/ethonline-sealed-bid
python3 scripts/skucheck.py                                    # GREEN = paid path is live (only needed if you run a paid SETTLE)
cast block-number --rpc-url https://sepolia.base.org           # RPC answers
cast call 0xaDF984468f5C7DEeb82FA4c98f25CA3952921ce7 "settlementCount()(uint256)" --rpc-url https://sepolia.base.org   # expect 1
cd sealed-bid-ts && bun test | tail -3 && cd ..                # 34 pass
```

Have open: README (diagram), the Basescan tx above, `EVIDENCE.md`, `workflow.ts`, `workflow.test.ts`.

**Fallbacks.** RPC down → the Basescan tab and `EVIDENCE.md` carry every read verbatim.
Simulator slow → play `evidence/demo-narrated.mp4` from 1:20 (the NO_OVERLAP section).
Screen share dies → the three screenshots in the showcase tell the same story in order.

## What NOT to do live

- Do not run a **paid** SETTLE unless asked; it takes ~90 s and spends ~$0.79. The live DON
  settlement on Basescan is stronger evidence than a fresh simulator run anyway.
- Do not `cre workflow activate` the deployed workflow during the demo — it would settle the
  same run every minute and email failures. It stays paused; the receipt is the proof.
- Do not show `.env`. The reserve values (120 / 90) are fine to say aloud; the point of the
  demo is that they appear in no output.
