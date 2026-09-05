Helper scripts.

| Script | Purpose |
|---|---|
| `deploy_receiver.py` | deploys `contracts/src/SealedBidReceiver.sol` to Base Sepolia from the forge artifact (`forge build` first). Reads `CRE_ETH_PRIVATE_KEY` from the process env or the gitignored repo `.env`; `RECEIVER_FORWARDER` / `RECEIVER_EXPECTED_OWNER` select the production or simulation instance (see `EVIDENCE.md`). `--dry-run` estimates only. |
| `skucheck.py` | (Task 5) smoke test that the paid BlindOracle path is live |
| `demo.py` | (Task 9) two-agent end-to-end driver |

```bash
pip install -r scripts/requirements.txt
python3 scripts/deploy_receiver.py --dry-run
```
