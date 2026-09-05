# SPEC — Sealed-Bid Agent Settlement

This is the only design document. Code is written from this file, not from any prior
implementation. Read it fully before touching `sealed-bid-ts/`.

## 1. Problem

Two autonomous agents want to trade. Each has a private reserve price:

- **Agent A (buyer)** will pay at most `A_max`.
- **Agent B (seller)** will accept at least `B_min`.

A trade is possible iff `B_min <= A_max`. Neither party wants to reveal its number, because
the other would simply price at the edge of it. A trusted middleman who sees both numbers
solves the maths and creates a new counterparty risk.

## 2. Protocol

### 2.1 Sealing (inside the enclave)

A Chainlink CRE **Confidential Workflow** runs in a TEE. Both reserve prices are CRE Vault
secrets and are only ever read inside the enclave (one `getSecrets` call, all ids at once).

The workflow computes:

```
if B_min <= A_max:
    clearing = (A_max + B_min) / 2          # midpoint of the overlap
    return SETTLE(clearing)
else:
    return NO_OVERLAP
```

**Output contract — this is the security property:**

| Result | What leaves the enclave | What must NOT leave |
|---|---|---|
| `SETTLE` | the clearing price, the run id, an attestation | `A_max`, `B_min` |
| `NO_OVERLAP` | the literal string `NO_OVERLAP`, the run id, an attestation | `A_max`, `B_min`, **and any ordering / distance hint** |

Edge cases (all unit-tested): equal bands (`A_max == B_min` → `SETTLE` at that price),
single-point overlap, non-numeric or missing secret (→ `INVALID_INPUT`, no partial output),
negative or zero price (→ `INVALID_INPUT`).

Note that `SETTLE(clearing)` necessarily reveals `A_max + B_min`. It does not reveal either
value alone, and `NO_OVERLAP` reveals nothing. This is the standard sealed-bid trade-off
and is stated in the README rather than hidden.

### 2.2 Attestation

Every run emits an **attestation record** the enclave signs:

```
{ run_id, result: SETTLE|NO_OVERLAP|INVALID_INPUT,
  clearing_price?: number,
  input_commitments: { a: sha256(A_max || salt_a), b: sha256(B_min || salt_b) },
  workflow_id, executed_at }
```

`input_commitments` let a party later prove *what it bid* without the enclave ever having
published it. The salts are also Vault secrets.

### 2.3 Settlement (on Base Sepolia)

On `SETTLE`, the workflow writes the clearing price to a receiver contract on Base Sepolia
via the CRE forwarder (`donRuntime.report` → `EVMClient`). Agent A's wallet then transfers
`clearing` to Agent B. On `NO_OVERLAP`, nothing is written on chain.

### 2.4 Adjudication via a neutral third party

Settlement is **not self-certified**. Every run is bracketed by paid calls to the public
BlindOracle API (`https://api.craigmbrown.com`, x402/USDC on Base). All calls go through
`bo_client.py`; each is logged with `sku, price, result, proof_id, changed_outcome`.

| Stage | Service | Price | Purpose |
|---|---|---|---|
| Pre-bid | `Agent Reputation Lookup` | $0.01 | is this counterparty known? |
| Pre-bid, high value | `Pre-Hire Agent Check` | $0.25 | deeper screen above a threshold |
| Bracket open | `Agent Trust Badge` | $0.01 | canonical proof-pair open |
| Post-settle | `Process-Followed Attestation` | $0.25 | proves the protocol steps were followed, in order |
| Contested | `Dispute Settlement — Neutral A2A Adjudication` | $5.00 | **neither party nor either operator decides** |
| Bracket close | `Agent Reputation Lookup` | $0.01 | canonical proof-pair close |

**Evidence rule (load-bearing).** The dispute service accepts `evidence: array[string]` in
the clear. A party MUST NOT submit its reserve price as evidence — that reveals it to the
arbiter and defeats the premise. Evidence is the enclave **attestation** plus the
`input_commitments` hashes. A test asserts no numeric reserve value appears in any dispute
payload.

The dispute service is billed on **Base mainnet** at 5 USDC per call. Budget ≈ $15 for the
demo. The `Process-Followed Attestation` expects:

```
declared_process: { ordered: true, required: [{id: "vet_a"}, {id: "vet_b"}, {id: "bracket_open"},
                    {id: "seal"}, {id: "settle_or_abort"}, {id: "bracket_close"}] }
run_evidence: [ {step_id, ts, prev_sha256, signature, sig_scheme, pubkey, ...}, ... ]
```

Records are emitted in the **RAP-1 wire format**
(<https://craigmbrown.com/blindoracle/resolution-attestation-profile.html> §7):
canonical JSON (sorted keys, no whitespace, non-ASCII escaped), `prev_sha256` over the
previous record minus its four signature fields, and a signature over the record minus
the same.

**Signed with ed25519** (`EVIDENCE_ED25519_PRIVATE_KEY`, gitignored). The public key is
published at `evidence/signing-key.pub`:

```
c151a183da6f5b52a141bbedb55218d048bd09649fe37560459be373913da6c2
```

A public key cannot sign, so a third party can verify a bundle without being able to
produce one, and the attestation reports `signature_binding: attributable`. Runs with
no key configured fall back to `hmac-sha256`, where the verification key travels in the
record and can also sign — tamper-evident, attributing nothing
(`signature_binding: tamper_evident_only`). The run announces which scheme it used; it
never downgrades silently.

**What this does and does not establish.** Nobody but the keyholder can produce a bundle
that verifies against the published key, and a bundle substituted from another run is
detectable. It does **not** establish who the keyholder is: the key is published by this
repo, which we control, so the identity behind it is self-asserted. Binding it to a
third-party registry is out of scope here — the ERC-8004 validation registry that would
be its natural home is not deployed on any chain (RAP-1 §8.1).

## 2.5 Prize qualification — Chainlink "Best Confidential Workflow" ($2,000; up to 2 teams × $1,000)

Read from `ethglobal.com/events/ethonline2026/prizes/chainlink` on 2026-09-04 (published; was
"Coming soon" on 09-02). Requirements, verbatim:

1. "Build a CRE Workflow that uses the Confidential Workflows to execute a meaningful part of
   the application."
2. "The workflow must register and use a confidential TEE handler, such as `handlerInTee` in
   TypeScript or `cre.HandlerInTee` in Go."
3. "The confidential portion of the workflow must process at least one sensitive input, secret,
   confidential API response, private parameter, or intermediate value inside the enclave."
4. "The Confidential Workflow must be meaningfully integrated into the project's core
   functionality. A placeholder handler or an isolated example that does not contribute to the
   application will not qualify."
5. "Demonstrate a successful execution through either: A Confidential Workflow simulation using
   the CRE CLI or a live deployment on the CRE network."
6. "Provide evidence of the successful simulation or deployment in the submission, such as a
   demo video, terminal output, execution logs, or deployment details."

**Design consequences (binding):**

- `sealed-bid-ts/main.ts` MUST register the overlap computation with **`handlerInTee`** — the
  sealing (§2.1) and the attestation (§2.2) run inside that handler, nothing else does the
  maths. This is the line a judge greps for.
- The enclave processes **two** sensitive inputs (both reserve prices, read via `getSecrets`
  inside the handler) and produces a sensitive **intermediate value** (the overlap) that never
  leaves. Req. 3 is satisfied three ways over; say so in the README.
- **Simulation is sufficient.** `cre workflow simulate` output is a first-class deliverable —
  capture the terminal output of a `SETTLE` run and a `NO_OVERLAP` run into `EVIDENCE.md`
  verbatim, plus the video. Live deploy (Phase 3) is a bonus, not a requirement.
- General ETHGlobal submission norms: public repo, working demo, video 2–5 minutes.

Other Chainlink prizes: "Best Chainlink-Powered Upgrade" ($500) is Continuity-track only — out
of scope. "Automated Liquidation Protection Challenge" ($500) was still "Coming soon" on 09-04
(details via Discord `#partner-chainlink`) — re-check on Sep 7; not pursued unless it costs
nothing beyond Phase 2.

## 3. Measured constraints of the runtime (design inputs, not guesses)

- `getSecrets`: **one call per execution, ≤ 9 ids**. A second call fails; 10+ ids fail.
- Confidential HTTP: ~10 s per request; the enclave lives ~50 s. Any async service call
  (council-style or dispute) is a **two-tick** step: tick 1 submits and parks the job id in
  our own state, tick 2 collects.
- The DON cannot reach `localhost`. Mocks and endpoints must be public TLS hosts.
- Workflow registry is on **Ethereum mainnet** (gas per deploy/link, sub-cent at low gwei);
  the org's quota is 3 workflows per linked key. Free a slot before the final onchain deploy.
- Prefer the synchronous $0.01 / $0.05 / $0.25 / $0.50 services on the hot path; reserve the
  $5 dispute call for the two-tick path.
- Do not run `cre update`; pin CLI v1.31.0.

## 4. Repository layout

```
sealed-bid-ts/main.ts          the confidential workflow (sealing + overlap + attestation)
sealed-bid-ts/main.test.ts     unit tests INCLUDING the no-leak assertions
sealed-bid-ts/workflow.yaml    workflow manifest
project.yaml                   CRE targets, pinned to ethereum-testnet-sepolia-base-1
secrets.yaml                   secret NAMES only
bo_client.py                   vet / bracket / attest / dispute over the public API
scripts/skucheck.py            smoke test: is the paid-service path live right now?
scripts/demo.py                two-agent end-to-end driver
contracts/                     Base Sepolia settlement receiver
DISCLOSURE.md                  pre-existing work + AI assistance statement
```

## 5. Tasks (one commit each, in order)

1. ✅ Init repo: LICENSE, README, DISCLOSURE, CLAUDE.md, SPEC, .gitignore, .env.sample
2. ✅ CRE skeleton: scaffold from the hello-confidential template shape with a registered
   `handlerInTee`; `project.yaml` on Base Sepolia; `cre workflow simulate` returns a result.
   (Done 2026-09-04: both reserves read in ONE `getSecrets` call inside the handler, validated
   in-enclave, `SEALED_INPUTS_OK` / `INVALID_INPUT` crosses to the DON; simulator reports
   "AWS Nitro in us-west-2". Note: the simulator requires every name in `secrets.yaml` to
   resolve, so that file grows per task rather than being declared up front.)
3. ✅ **No-leak tests first**: (a) neither reserve appears in any non-enclave log line or return
   value; (b) `NO_OVERLAP` output is byte-identical regardless of how far apart the bands
   are. Both must FAIL against a deliberately leaky stub, then pass.
   (Done 2026-09-04. `noleak.ts` holds the checkers; the handler is `runSealedBid(runtime, seal)`
   so the suite runs the identical output path against two deliberately leaky seals as
   permanent NEGATIVE CONTROLS — `leakySealReturnsReserve` and `leakySealRevealsGap` — and asserts
   the checkers catch them on every run. Checks cover logs, the return string, decoded report
   string fields, and the uint report field. `sealBids` is a validated placeholder returning
   `NO_OVERLAP`; Task 4 replaces one marked line and must keep this suite green.)
4. ✅ Overlap logic + attestation per §2.1–2.2, entirely inside the `handlerInTee` handler.
   Unit tests: overlap / no-overlap / equal / single-point / invalid input.
   (Done 2026-09-04. `sealBids`: overlap iff `sellerMin <= buyerMax`, midpoint clears. Commitments
   `sha256(utf8("<reserve>|<salt>"))` with the salts as Vault secrets (4 ids in the one `getSecrets`
   call); report ABI `(string result, uint256 clearingPriceMicro, string runLabel, bytes32
   commitmentA, bytes32 commitmentB, bytes32 runId)`. **Deviation from §2.2:** `executed_at` is
   not emitted — a wall-clock value is non-deterministic across DON nodes and would break both
   consensus and the byte-identity property; the settlement tx's block timestamp serves. The
   no-leak rule was sharpened to "output ⊆ f(inputs)": on SETTLE the clearing price must be the
   protocol midpoint, and a reserve may appear only as that clearing price (equal bands
   necessarily settle at the shared value). 27/27 tests; all three paths captured in
   `EVIDENCE.md`.)
5. ✅ `bo_client.py` + `scripts/skucheck.py`; real paid calls against the live API; record
   settlement evidence (tx hashes) in `EVIDENCE.md`.
   (Done 2026-09-05. Client pays x402 `exact` challenges with an EIP-3009 USDC authorization on
   Base mainnet via the `x402` PyPI SDK; six calls from §2.4 wrapped; one `bo-value` ledger row
   and one `evidence/bo/*.json` deliverable per paid call. Evidence rule enforced in code:
   `build_dispute_evidence()` emits attestation fields only and `assert_no_reserve()` refuses a
   payload carrying a reserve; `dispute()` is gated on an explicit $5 opt-in. **Nine real paid
   calls settled on Base mainnet** (3 distinct SKUs: reputation ×5, badge ×2, pre-hire ×2, $0.57
   total), each a USDC transfer from the payer to the treasury submitted gaslessly by the
   facilitator — verified from the chain, not from the API. **Disclosure the proof rail makes
   itself:** the payer wallet is one the BlindOracle operator also uses, so the rail marks these
   settlements `payer_class: self` and deliberately issues no reputation-bearing proof for them;
   they are real transfers, not reputation evidence. Two demo passports registered
   (`sealedbid-agent-a` = `agent_69cdcf6a04fa`, `sealedbid-agent-b` = `agent_295d689e1f02`), both
   observer tier with honest zero history. 11 offline tests.)
6. ✅ Settlement receiver on Base Sepolia; `SETTLE` writes on chain, `NO_OVERLAP` writes nothing.
   (Done 2026-09-05. `contracts/src/SealedBidReceiver.sol` accepts `onReport` only from its immutable
   Forwarder, optionally only from a pinned workflow owner, and records ONLY a `SETTLE` — every other
   result reverts, so a non-settling run cannot leave a trace even if a workflow tried. The workflow
   calls `EVMClient.writeReport` on SETTLE alone. **Measured deviation from §2.3 as written:** the CRE
   simulator's `--broadcast` signs through its own mock forwarder `0x82300bd7…dF3e5` as placeholder
   owner `0xaaaa…aaaa`, so the production receiver (`0xaDF9…1ce7`, trusts the CRE Forwarder
   `0xF8344CFd…4482`) correctly rejected it. A second, simulation-only receiver (`0xA2eB…0590`) pinned
   to the mock forwarder is what `simulation-settings` writes to; a live `cre workflow deploy` targets
   the production one. The clearing price and both commitments are on Base Sepolia at
   tx `0x6a02d0ca…34c9`; the NO_OVERLAP run sent no transaction. 13 forge + 34 bun tests. The "Agent A's
   wallet then transfers `clearing` to Agent B" half of §2.3 is the demo driver's job (Task 9) — the
   receiver records the price the agents are bound to; it does not custody funds.)
7. Security review before each push: no secrets, no private-repo references, real history.
8. *(optional)* Dispute routing + process attestation per §2.4.
   (Done 2026-09-05. Process attestation is bought by `scripts/demo.py --attest`. The first run
   returned `non_conformant` — A6 chain linkage **fail**, A7 signatures **unverifiable** — because
   the record format the service verifies for A6/A7 was not published to callers; recorded as-is
   in `EVIDENCE.md` and not retried by guessing. BlindOracle then published it as RAP-1 §7, our
   producer was corrected to match, and the chain moved to ed25519. Re-run over the wire:
   **`conformant`, A6 pass, A7 pass, `signature_binding: attributable`**
   (tx `0xa562f6f727264c49cee661adbc05bbb08d71692ad28b37ecc7f611bcf27a1fc9`). Dispute routing is built and tested
   (`bo_client.dispute`, evidence rule enforced, $5 opt-in) but not exercised: a real dispute costs
   5 USDC on mainnet and there is no contested outcome to adjudicate.)
9. Submission: README architecture diagram, 3-minute video recorded while `skucheck` is
   green, submit before **2026-09-13 16:00 UTC**.
   (Driver + diagram done 2026-09-05: `scripts/demo.py` runs the protocol in one command and both
   outcomes are recorded under `evidence/demo/`; README carries the mermaid architecture diagram;
   `docs/VIDEO-SCRIPT.md` is the 3-minute walkthrough. Remaining: record the video, submit.)

## 6. Acceptance criteria

- [ ] ≥ 12 commits spread across Sep 4–13, ≥ 2 on every event day, none before kick-off
- [ ] `cre workflow simulate` returns `SETTLE` on overlapping bands, `NO_OVERLAP` otherwise
- [ ] No-leak tests pass and demonstrably fail against a leaky stub
- [ ] ≥ 3 distinct paid BlindOracle services called for real, with settlement evidence recorded
- [ ] Settlement executes on Base Sepolia on the `SETTLE` path
- [ ] `DISCLOSURE.md` accurate; no secret and no private-repo reference in the repo
- [ ] Submitted before 2026-09-13 16:00 UTC

## 7. Schedule

| Phase | Dates | Deliverable |
|---|---|---|
| 1 Foundation | Sep 4–6 | repo, CRE skeleton simulating, no-leak tests written |
| 2 Core (minimum submission) | Sep 7–10 | overlap logic, service client + bracket, settlement on Base Sepolia |
| 3 Polish (optional) | Sep 11–13 | dispute routing, attestation, diagram, video |

Check-ins: Sep 7 23:59 ET, Sep 10 23:59 ET.
