Helper scripts.

| Script | Purpose |
|---|---|
| `deploy_receiver.py` | deploys `contracts/src/SealedBidReceiver.sol` to Base Sepolia from the forge artifact (`forge build` first). Reads `CRE_ETH_PRIVATE_KEY` from the process env or the gitignored repo `.env`; `RECEIVER_FORWARDER` / `RECEIVER_EXPECTED_OWNER` select the production or simulation instance (see `EVIDENCE.md`). `--dry-run` estimates only. |
| `skucheck.py` | start-of-session smoke test: catalog lists the five SKUs at the expected prices, an unpaid call 402s with an `exact` USDC challenge on Base mainnet, the proof rail answers, the payer holds enough USDC for one bracket. `--paid` spends $0.01 on a real probe. Exit 0 = GREEN. |
| `demo.py` | (Task 9) two-agent end-to-end driver |

```bash
pip install -r scripts/requirements.txt
python3 scripts/skucheck.py                     # free
python3 scripts/deploy_receiver.py --dry-run    # free
python3 ../bo_client.py catalog                 # free; `reputation <agent>` costs $0.01
```
