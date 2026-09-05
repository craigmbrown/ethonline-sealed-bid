#!/usr/bin/env python3
"""Deploy contracts/src/SealedBidReceiver.sol to Base Sepolia.

Reads the forge build artifact (run `forge build` in contracts/ first), signs the
creation transaction with CRE_ETH_PRIVATE_KEY (the CRE workflow owner key — the
same key the simulator broadcasts with), waits for the receipt and prints the
address. Nothing here is specific to the sealed-bid logic; it is a plain
create-transaction sender.

Environment (process env, or a gitignored .env next to the repo root):
  BASE_SEPOLIA_RPC_URL   default https://sepolia.base.org
  CRE_ETH_PRIVATE_KEY    0x-prefixed hex; NEVER printed, NEVER logged
  RECEIVER_FORWARDER     Chainlink CRE Forwarder on Base Sepolia
                         default 0xF8344CFd5c43616a4366C34E3EEE75af79a74482
  RECEIVER_EXPECTED_OWNER  workflow owner the receiver accepts reports from
                         default: the deployer address; pass 0x000…000 to disable

Usage:
  python3 scripts/deploy_receiver.py            # deploy
  python3 scripts/deploy_receiver.py --dry-run  # estimate only, no transaction
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ARTIFACT = REPO / "contracts" / "out" / "SealedBidReceiver.sol" / "SealedBidReceiver.json"
DEFAULT_RPC = "https://sepolia.base.org"
DEFAULT_FORWARDER = "0xF8344CFd5c43616a4366C34E3EEE75af79a74482"
BASE_SEPOLIA_CHAIN_ID = 84532
ZERO = "0x0000000000000000000000000000000000000000"


def _load_env() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv(REPO / ".env", override=False)
    except ImportError:
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="estimate gas and print the plan; send nothing")
    args = ap.parse_args()

    _load_env()
    from eth_account import Account
    from web3 import Web3

    if not ARTIFACT.exists():
        print(f"artifact missing: {ARTIFACT.relative_to(REPO)} — run `forge build` in contracts/", file=sys.stderr)
        return 2
    artifact = json.loads(ARTIFACT.read_text())
    abi, bytecode = artifact["abi"], artifact["bytecode"]["object"]

    key = os.environ.get("CRE_ETH_PRIVATE_KEY", "")
    if not key.startswith("0x") or len(key) != 66:
        print("CRE_ETH_PRIVATE_KEY missing or not 0x-prefixed 32-byte hex", file=sys.stderr)
        return 2
    account = Account.from_key(key)

    rpc = os.environ.get("BASE_SEPOLIA_RPC_URL", DEFAULT_RPC)
    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 60}))
    chain_id = w3.eth.chain_id
    if chain_id != BASE_SEPOLIA_CHAIN_ID:
        print(f"refusing: rpc chain id {chain_id} is not Base Sepolia ({BASE_SEPOLIA_CHAIN_ID})", file=sys.stderr)
        return 2

    forwarder = Web3.to_checksum_address(os.environ.get("RECEIVER_FORWARDER", DEFAULT_FORWARDER))
    expected_owner = Web3.to_checksum_address(os.environ.get("RECEIVER_EXPECTED_OWNER", account.address))
    if w3.eth.get_code(forwarder) in (b"", b"\x00"):
        print(f"refusing: no contract code at forwarder {forwarder} on chain {chain_id}", file=sys.stderr)
        return 2

    contract = w3.eth.contract(abi=abi, bytecode=bytecode)
    tx = contract.constructor(forwarder, expected_owner).build_transaction(
        {
            "from": account.address,
            "nonce": w3.eth.get_transaction_count(account.address),
            "chainId": chain_id,
        }
    )
    balance = w3.eth.get_balance(account.address)
    gas = w3.eth.estimate_gas(tx)
    tx["gas"] = int(gas * 1.2)
    max_cost_wei = tx["gas"] * int(tx.get("maxFeePerGas") or tx.get("gasPrice") or 0)

    print(f"deployer          {account.address}")
    print(f"balance           {w3.from_wei(balance, 'ether')} ETH")
    print(f"forwarder         {forwarder}")
    print(f"expected owner    {expected_owner if expected_owner != ZERO else '(disabled)'}")
    print(f"gas estimate      {gas}  (limit {tx['gas']}, worst-case {w3.from_wei(max_cost_wei, 'ether')} ETH)")
    if balance < max_cost_wei:
        print("refusing: balance below worst-case deploy cost", file=sys.stderr)
        return 2
    if args.dry_run:
        print("dry run — nothing sent")
        return 0

    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"sent              {tx_hash.hex()}")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
    if receipt.status != 1:
        print(f"deploy REVERTED in block {receipt.blockNumber}", file=sys.stderr)
        return 1
    print(f"deployed          {receipt.contractAddress}")
    print(f"block             {receipt.blockNumber}  gas used {receipt.gasUsed}")
    print(f"explorer          https://sepolia.basescan.org/address/{receipt.contractAddress}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
