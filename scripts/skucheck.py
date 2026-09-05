#!/usr/bin/env python3
"""skucheck — is the paid BlindOracle path live RIGHT NOW? Run at the start of every session.

Free checks (no payment, no key):
  1. the catalog answers and lists every SKU the protocol uses, with its price
  2. a paid route answers HTTP 402 with an `exact` USDC challenge on Base mainnet
     (eip155:8453) — the settlement rail is up
  3. the public proof rail answers
  4. the payer wallet (if a key is configured) holds enough USDC on Base for one
     full bracket: vet + badge + pre-hire + attestation + close

With --paid it additionally spends $0.01 on one real reputation lookup and checks
the response carries a settlement reference, then prints the proof-rail row for it.

Exit 0 = green, 1 = something is down or underfunded (do not record the demo video).
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bo_client import (
    CALL_LOG,
    CATALOG_PRICE_USD,
    SKU,
    USDC_BASE,
    BoClient,
)

BASE_RPC = "https://mainnet.base.org"
BRACKET_USD = sum(CATALOG_PRICE_USD[s] for s in SKU.values() if s != SKU["dispute"])


def ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def bad(msg: str) -> None:
    print(f"  ✗ {msg}")


def check_catalog(c: BoClient) -> bool:
    try:
        cat = c.catalog()
    except Exception as e:  # noqa: BLE001
        bad(f"catalog unreachable: {e}")
        return False
    by_id = {s.get("sku_id"): s for s in cat.get("services", [])}
    good = True
    for sku in SKU.values():
        s = by_id.get(sku)
        if not s:
            bad(f"{sku} missing from the catalog")
            good = False
            continue
        price = s.get("price_usd")
        flag = "" if price == CATALOG_PRICE_USD[sku] else f"  (price changed: catalog {price}, expected {CATALOG_PRICE_USD[sku]})"
        ok(f"{sku:36} ${price}{flag}")
        if flag:
            good = False
    disc = cat.get("dispute_disclosure", {})
    if disc:
        print(f"  · dispute disclosure: adjudicator={disc.get('adjudicator')} neutrality={disc.get('neutrality')}")
    return good


def check_402(c: BoClient) -> bool:
    r = requests.post(f"{c.base}/v1/services/{SKU['reputation']}", json={"task": "agent_id: skucheck"}, timeout=30)
    if r.status_code != 402:
        bad(f"expected 402 from an unpaid call, got {r.status_code}")
        return False
    hdr = r.headers.get("payment-required")
    try:
        chal = json.loads(base64.b64decode(hdr + "==")) if hdr and not hdr.startswith("{") else (json.loads(hdr) if hdr else r.json())
    except Exception:  # noqa: BLE001
        chal = r.json()
    accepts = chal.get("accepts", [])
    exact = [a for a in accepts if a.get("scheme") == "exact" and a.get("network") == "eip155:8453"]
    if not exact:
        bad(f"no exact/eip155:8453 accept in the challenge: {accepts}")
        return False
    a = exact[0]
    usdc_ok = a.get("asset", "").lower() == USDC_BASE.lower()
    ok(f"402 challenge: exact, Base mainnet, amount={a.get('amount')} (micro-USDC), payTo={a.get('payTo')}, asset={'USDC' if usdc_ok else a.get('asset')}")
    return usdc_ok


def check_proof_rail(c: BoClient) -> bool:
    r = requests.get(f"{c.base}/v1/proofs/settlements?limit=1", timeout=30)
    if r.status_code != 200:
        bad(f"proof rail returned {r.status_code}")
        return False
    d = r.json()
    ok(f"proof rail up: {d.get('scanned')} settlements scanned, latest kind {d.get('proofs', [{}])[0].get('kind')}")
    return True


def check_funding(c: BoClient) -> bool:
    if not c.payer_address:
        print("  · no payer key configured — paid calls will 402 (set BLINDORACLE_PAYER_PRIVATE_KEY or AGENT_A_PRIVATE_KEY)")
        return True
    from web3 import Web3

    w3 = Web3(Web3.HTTPProvider(BASE_RPC, request_kwargs={"timeout": 30}))
    usdc = w3.eth.contract(
        address=Web3.to_checksum_address(USDC_BASE),
        abi=[{"name": "balanceOf", "type": "function", "stateMutability": "view", "inputs": [{"name": "a", "type": "address"}], "outputs": [{"name": "", "type": "uint256"}]}],
    )
    bal = usdc.functions.balanceOf(c.payer_address).call() / 1_000_000
    enough = bal >= BRACKET_USD
    (ok if enough else bad)(f"payer {c.payer_address} holds {bal:.2f} USDC on Base; one bracket costs ${BRACKET_USD:.2f}")
    return enough


def paid_probe(c: BoClient) -> bool:
    if not c.payer_address:
        bad("--paid needs a payer key")
        return False
    res = c.reputation_lookup("skucheck-probe", note="skucheck --paid")
    if not res.ok:
        bad(f"paid probe failed: HTTP {res.status} {json.dumps(res.body)[:300]}")
        return False
    ok(f"paid probe ok: tx={res.tx_hash} proof_id={res.proof_id} sha256={res.content_sha256}")
    if res.tx_hash:
        p = c.settlement_proof(res.tx_hash)
        (ok if p.get("status") == 200 else bad)(f"proof rail row for the tx: status {p.get('status')} kind {p.get('kind')} rail {p.get('rail')}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--paid", action="store_true", help="also spend $0.01 on one real reputation lookup")
    args = ap.parse_args()
    c = BoClient(call_log=CALL_LOG if args.paid else None)
    print(f"skucheck against {c.base}")
    results = [check_catalog(c), check_402(c), check_proof_rail(c), check_funding(c)]
    if args.paid:
        results.append(paid_probe(c))
    green = all(results)
    print("GREEN — the paid path is live" if green else "RED — do not record the demo until this is green")
    return 0 if green else 1


if __name__ == "__main__":
    sys.exit(main())
