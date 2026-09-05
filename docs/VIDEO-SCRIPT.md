# Demo video — 3 minutes, one terminal, one browser tab

Record while `python3 scripts/skucheck.py` is GREEN. Everything below is a real command with a
real result; nothing is mocked. Suggested layout: terminal left, Basescan tab right.

| t | Say | Show |
|---|---|---|
| 0:00 | "Two agents want to trade. Each has a private reserve price. Today's agent commerce makes them either reveal it or trust a middleman who sees both. We seal both numbers in a Chainlink CRE confidential workflow instead." | README diagram |
| 0:25 | "The reserves are Vault secrets. They are read inside the enclave, once, and never leave. Here is the handler — `cre.handlerInTee`, one `getSecrets` call, the overlap maths, salted commitments." | `sealed-bid-ts/workflow.ts` lines around `runSealedBid` |
| 0:50 | "The tests are the security property. Two deliberately leaky implementations live in the suite as negative controls — if they ever pass, the checkers are broken." | `cd sealed-bid-ts && bun test` → `34 pass` |
| 1:05 | "Now the whole protocol, for real. Each agent vets the other through BlindOracle, a public pay-per-call API, paid in USDC on Base with x402. Then the enclave runs." | `python3 scripts/demo.py --buyer-max 120 --seller-min 90 --broadcast --settle-transfer` |
| 1:35 | "SETTLE at 105 — the midpoint. Neither 120 nor 90 appears anywhere. The DON wrote the result to the receiver on Base Sepolia, and Agent A paid Agent B." | the `settle` and `transfer` lines; open the transfer tx on Basescan: value 105000000, calldata = run id |
| 2:05 | "Now the other case." | `python3 scripts/demo.py --buyer-max 90 --seller-min 120 --broadcast --no-pay` → `NO_OVERLAP: nothing written on chain, nothing transferred` |
| 2:20 | "NO_OVERLAP carries nothing — not the gap, not which side was higher. No transaction was sent. And the receiver contract itself reverts anything that is not a SETTLE, so even a misbehaving workflow could not leave a trace." | `contracts/test` → `13 passed` |
| 2:40 | "Every payment is a USDC transfer you can check on Basescan without trusting us; every settlement is a contract read. The one honest caveat: SETTLE reveals the sum of the two reserves. That is the standard sealed-bid trade-off, and it is stated in the README." | `EVIDENCE.md` scrolled |
| 2:55 | "Sealed-bid agent settlement. Chainlink CRE, Base, BlindOracle. Thanks." | repo URL |

## Before recording

```bash
python3 scripts/skucheck.py                # must print GREEN
cast balance 0x2D0B6cd9485e59a6eDc10B048227FAF0e81D174D --rpc-url https://sepolia.base.org --ether   # > 0.0005
```

## What not to show

- The `.env` file, ever.
- The reserve values are fine to say out loud (they are the demo inputs) but the point of 1:35 is that
  they appear in no output — pause on that.
