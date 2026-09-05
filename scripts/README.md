Helper scripts.

| Script | Purpose |
|---|---|
| `deploy_receiver.py` | deploys `contracts/src/SealedBidReceiver.sol` to Base Sepolia from the forge artifact (`forge build` first). Reads `CRE_ETH_PRIVATE_KEY` from the process env or the gitignored repo `.env`; `RECEIVER_FORWARDER` / `RECEIVER_EXPECTED_OWNER` select the production or simulation instance (see `EVIDENCE.md`). `--dry-run` estimates only. |
| `skucheck.py` | start-of-session smoke test: catalog lists the five SKUs at the expected prices, an unpaid call 402s with an `exact` USDC challenge on Base mainnet, the proof rail answers, the payer holds enough USDC for one bracket. `--paid` spends $0.01 on a real probe. Exit 0 = GREEN. |
| `demo.py` | the two-agent protocol in one command: vet both parties, bracket open, seal in the enclave, settle (read the receiver back, optionally A pays B on Base Sepolia) or abort, optional process attestation, bracket close. Hash-chained + HMAC-signed evidence per step, written to `evidence/demo/<runId>.json` only after `assert_no_reserve` passes. `--no-pay` for a free run, `--broadcast` for a real chain write, `--settle-transfer`, `--attest`. |

```bash
pip install -r scripts/requirements.txt
python3 scripts/skucheck.py                     # free
python3 scripts/deploy_receiver.py --dry-run    # free
python3 ../bo_client.py catalog                 # free; `reputation <agent>` costs $0.01
```
