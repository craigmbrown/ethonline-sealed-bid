#!/usr/bin/env python3
"""demo — the two-agent sealed-bid settlement, end to end (SPEC.md §2).

    vet_a ─► vet_b ─► bracket_open ─► seal (enclave) ─► settle_or_abort ─► [attest] ─► bracket_close

Agent A (buyer) holds A_max, Agent B (seller) holds B_min. Neither number ever leaves this
process except as a CRE Vault secret handed to the simulator's enclave. Everything that IS
written — the evidence chain, the attestation payload, the on-chain record — is checked
against both reserves with `bo_client.assert_no_reserve` before it is persisted or sent.

Steps and what they cost:
  vet_a / vet_b       reputation.lookup on the counterparty            $0.01 each
  bracket_open        agent.trust-badge on the buyer, task_id=run_id   $0.01
  seal                cre workflow simulate (--broadcast writes the SETTLE to Base Sepolia)  free / testnet gas
  settle_or_abort     on SETTLE: read the receiver back; optionally A → B transfer on Base Sepolia
  attest (optional)   security.process-attestation over the signed evidence chain          $0.25
  bracket_close       reputation.lookup on the counterparty            $0.01

Usage:
  python3 scripts/demo.py --buyer-max 120 --seller-min 90                # simulate, no chain write, paid vets
  python3 scripts/demo.py --buyer-max 120 --seller-min 90 --no-pay        # free: skips every BlindOracle call
  python3 scripts/demo.py --buyer-max 120 --seller-min 90 --broadcast --settle-transfer --attest
  python3 scripts/demo.py --buyer-max 90 --seller-min 120 --broadcast     # NO_OVERLAP: nothing written anywhere

Evidence is written to evidence/demo/<run_id>.json. The reserves are not in it.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import bo_client as bc

DEMO_EVIDENCE = REPO / "evidence" / "demo"
SIM_RECEIVER = "0xA2eB7d6EEd6a4d0976cb29B226093E007E720590"  # trusts the simulator's mock forwarder
BASE_SEPOLIA_RPC = "https://sepolia.base.org"
PRICE_SCALE = 1_000_000
STEP_IDS = ["vet_a", "vet_b", "bracket_open", "seal", "settle_or_abort", "bracket_close"]
RECEIVER_ABI = [
    {"name": "getSettlement", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "runId", "type": "bytes32"}],
     "outputs": [{"name": "", "type": "tuple", "components": [
         {"name": "clearingPriceMicro", "type": "uint256"}, {"name": "commitmentA", "type": "bytes32"},
         {"name": "commitmentB", "type": "bytes32"}, {"name": "recordedAt", "type": "uint64"}]}]},
    {"name": "settlementCount", "type": "function", "stateMutability": "view", "inputs": [],
     "outputs": [{"name": "", "type": "uint256"}]},
]


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ─── Evidence chain (SPEC §2.4): hash-chained, ed25519-signed (HMAC fallback), RAP-1 wire format ──


def canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def rap1_canonical(obj: Any) -> bytes:
    """RAP-1 §7.1 canonical JSON. Note `ensure_ascii` is left at its default (True):
    the verifier escapes non-ASCII as \\uXXXX, and a producer that does not will
    compute a different digest and fail A6/A7 on any accented character."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


# Fields excluded before hashing/signing, per RAP-1 §7.2 / §7.3. Excluding them is
# what makes the chain computable in one forward pass — a record's hash cannot
# depend on its own signature.
_RAP1_CHAIN_EXCLUDE = ("prev_sha256", "signature", "sig_scheme", "pubkey")
_RAP1_SIG_EXCLUDE = ("signature", "sig_scheme", "pubkey")


def _rap1_bytes(rec: dict[str, Any], exclude: tuple[str, ...]) -> bytes:
    return rap1_canonical({k: v for k, v in rec.items() if k not in exclude})


SIGNING_KEY_PUB_PATH = Path(__file__).resolve().parent.parent / "evidence" / "signing-key.pub"


def published_signing_pubkey() -> str | None:
    """The ed25519 public key this repo publishes, or None. Reading it lets a run
    assert that what it signed with is what a verifier was told to expect."""
    try:
        return SIGNING_KEY_PUB_PATH.read_text().strip() or None
    except OSError:
        return None


class EvidenceChain:
    """Hash-chained, signed, RAP-1 wire format
    (https://craigmbrown.com/blindoracle/resolution-attestation-profile.html §7).

    Previously this used the field name `hmac`, hashed the FULL previous record, and
    keyed the HMAC with `bytes.fromhex(key)`. All three differ from the published
    profile, so `security.process-attestation` returned A6 `chain_broken` and A7
    `no signed records submitted` on 2026-09-05 — recorded in EVIDENCE.md.

    Signature scheme (SPEC §2.4):

    * **ed25519** when `EVIDENCE_ED25519_PRIVATE_KEY` is set — the default for our runs.
      The public key is published at `evidence/signing-key.pub` and cannot sign, so a
      third party can verify a bundle without being able to forge one. RAP-1 reports
      `signature_binding: attributable`.
    * **hmac-sha256** otherwise, so a fresh clone with no key still produces a valid
      chain. There the verification key travels inside the record and can also sign, so
      the bundle is tamper-evident but attributes nothing —
      `signature_binding: tamper_evident_only`.

    ⚠️ What ed25519 buys, precisely: nobody but the keyholder can produce a bundle that
    verifies against the published key, and a substituted bundle from a later run is
    detectable. It does NOT establish who the keyholder is — the key is published by
    this repo, which we control, so identity here is self-asserted. Binding it to a
    third-party registry is a separate problem (RAP-1 §8.1)."""

    def __init__(self, secret: str, run_label: str) -> None:
        self.records: list[dict[str, Any]] = []
        priv = (os.environ.get("EVIDENCE_ED25519_PRIVATE_KEY") or "").strip()
        self._sk = None
        if priv:
            try:
                from cryptography.hazmat.primitives import serialization as _ser
                from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
                self._sk = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(priv))
                self.scheme = "ed25519"
                self.key = self._sk.public_key().public_bytes(
                    _ser.Encoding.Raw, _ser.PublicFormat.Raw).hex()
            except Exception as exc:  # bad hex, wrong length, library absent
                # Fail LOUD rather than silently downgrading: a run that believes it is
                # attributable and is not would misrepresent its own evidence.
                raise SystemExit(
                    f"EVIDENCE_ED25519_PRIVATE_KEY is set but unusable ({type(exc).__name__}). "
                    f"Fix it or unset it to fall back to hmac-sha256.") from exc
            expected = published_signing_pubkey()
            if expected and expected != self.key:
                raise SystemExit(
                    "signing key does not match the published evidence/signing-key.pub — "
                    "a verifier told to expect the published key would reject this run.")
        else:
            self.scheme = "hmac-sha256"
            self.key = hashlib.sha256((secret + "|" + run_label).encode()).hexdigest()

    def _sign(self, payload: bytes) -> str:
        if self._sk is not None:
            return self._sk.sign(payload).hex()
        # RAP-1 §7.3: the HMAC key is the `pubkey` STRING, UTF-8 encoded — not the
        # hex-decoded bytes. Matching this byte-for-byte is required or A7 fails.
        return hmac.new(self.key.encode("utf-8"), payload, hashlib.sha256).hexdigest()

    def append(self, step_id: str, **detail: Any) -> dict[str, Any]:
        rec: dict[str, Any] = {"step_id": step_id, "ts": now_iso(), **detail}
        if self.records:
            rec["prev_sha256"] = hashlib.sha256(
                _rap1_bytes(self.records[-1], _RAP1_CHAIN_EXCLUDE)).hexdigest()
        rec["signature"] = self._sign(_rap1_bytes(rec, _RAP1_SIG_EXCLUDE))
        rec["sig_scheme"] = self.scheme
        rec["pubkey"] = self.key
        self.records.append(rec)
        return rec

    @staticmethod
    def verify(records: list[dict[str, Any]]) -> bool:
        """Local check using the same rules the remote verifier applies. An
        unsupported scheme or unusable key material returns False here rather than
        silently passing — this is our own chain, so we know what it should be."""
        for idx, rec in enumerate(records):
            if idx > 0:
                expect_prev = hashlib.sha256(
                    _rap1_bytes(records[idx - 1], _RAP1_CHAIN_EXCLUDE)).hexdigest()
                if rec.get("prev_sha256") != expect_prev:
                    return False
            payload = _rap1_bytes(rec, _RAP1_SIG_EXCLUDE)
            pubkey = str(rec.get("pubkey", ""))
            sig = str(rec.get("signature", ""))
            scheme = rec.get("sig_scheme") or "hmac-sha256"
            if scheme == "ed25519":
                try:
                    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
                    Ed25519PublicKey.from_public_bytes(bytes.fromhex(pubkey)).verify(
                        bytes.fromhex(sig), payload)
                except Exception:  # bad signature, bad hex, or library absent
                    return False
            else:
                expect = hmac.new(pubkey.encode("utf-8"), payload, hashlib.sha256).hexdigest()
                if not hmac.compare_digest(expect, sig):
                    return False
        return True


# ─── The enclave step ──────────────────────────────────────────────────────

SIM_RESULT_RE = re.compile(r'Workflow Simulation Result:\s*\n\s*"([^"]*)"')
RUN_ID_RE = re.compile(r"enclave: outcome=(\w+) runId=(0x[0-9a-fA-F]{64})")
COMMIT_RE = re.compile(r"enclave: commitmentA=(0x[0-9a-fA-F]{64}) commitmentB=(0x[0-9a-fA-F]{64})")
TX_RE = re.compile(r"settlement: written to (0x[0-9a-fA-F]{40}) tx=(0x[0-9a-fA-F]{64})")


def parse_simulation(output: str) -> dict[str, Any]:
    """Pull outcome, run id, commitments, clearing price and tx out of the simulator's output."""
    res = SIM_RESULT_RE.search(output)
    run = RUN_ID_RE.search(output)
    com = COMMIT_RE.search(output)
    tx = TX_RE.search(output)
    out: dict[str, Any] = {
        "result_line": res.group(1) if res else None,
        "outcome": run.group(1) if run else None,
        "run_id": run.group(2) if run else None,
        "commitment_a": com.group(1) if com else None,
        "commitment_b": com.group(2) if com else None,
        "receiver": tx.group(1) if tx else None,
        "settlement_tx": tx.group(2) if tx else None,
        "clearing_price": None,
    }
    if out["result_line"]:
        m = re.match(r"SETTLE @ ([0-9.]+)", out["result_line"])
        if m:
            out["clearing_price"] = float(m.group(1))
    return out


def run_enclave(buyer_max: float, seller_min: float, salt_a: str, salt_b: str, broadcast: bool, target: str) -> tuple[str, dict[str, Any]]:
    env = dict(os.environ)
    env.update({
        "RESERVE_PRICE_AGENT_A_STAGING": repr(buyer_max) if not float(buyer_max).is_integer() else str(int(buyer_max)),
        "RESERVE_PRICE_AGENT_B_STAGING": repr(seller_min) if not float(seller_min).is_integer() else str(int(seller_min)),
        "COMMITMENT_SALT_A_STAGING": salt_a,
        "COMMITMENT_SALT_B_STAGING": salt_b,
    })
    cmd = ["cre", "workflow", "simulate", "./sealed-bid-ts", f"--target={target}", "--non-interactive", "--trigger-index", "0"]
    if broadcast:
        cmd += ["--broadcast", "-e", str(REPO / ".env")]
    proc = subprocess.run(cmd, cwd=REPO, env=env, capture_output=True, text=True, timeout=600, check=False)
    output = proc.stdout + "\n" + proc.stderr
    parsed = parse_simulation(output)
    parsed["exit_code"] = proc.returncode
    return output, parsed


# ─── Chain reads / the A → B transfer ──────────────────────────────────────


def read_receiver(run_id: str, receiver: str = SIM_RECEIVER, settlement_tx: str | None = None) -> dict[str, Any]:
    """Read the SETTLE record back. If the settlement tx hash is known, wait for it to be mined
    first — the simulator returns as soon as the forwarder tx is submitted, and a read in the
    same second sees the pre-settlement state (measured: clearing=0, then the row appeared)."""
    from web3 import Web3

    w3 = Web3(Web3.HTTPProvider(BASE_SEPOLIA_RPC, request_kwargs={"timeout": 60}))
    if settlement_tx and int(settlement_tx, 16) != 0:
        w3.eth.wait_for_transaction_receipt(settlement_tx, timeout=180)
    c = w3.eth.contract(address=Web3.to_checksum_address(receiver), abi=RECEIVER_ABI)
    s = c.functions.getSettlement(bytes.fromhex(run_id[2:])).call()
    for _ in range(12):  # RPC nodes can lag the receipt by a block or two
        if int(s[3]) != 0:
            break
        time.sleep(5)
        s = c.functions.getSettlement(bytes.fromhex(run_id[2:])).call()
    return {"receiver": receiver, "clearing_price_micro": int(s[0]), "commitment_a": "0x" + bytes(s[1]).hex(),
            "commitment_b": "0x" + bytes(s[2]).hex(), "recorded_at": int(s[3]), "settlement_count": int(c.functions.settlementCount().call())}


def settle_transfer(clearing_price_micro: int, run_id: str, payer_key: str, payee: str) -> dict[str, Any]:
    """Agent A pays Agent B on Base Sepolia (SPEC §2.3). The demo transfers `clearing` in
    micro-units as wei — a symbolic, real, testnet transaction that names the run id in its
    calldata so it can be tied to the receiver's record."""
    from eth_account import Account
    from web3 import Web3

    w3 = Web3(Web3.HTTPProvider(BASE_SEPOLIA_RPC, request_kwargs={"timeout": 60}))
    acct = Account.from_key(payer_key)
    tx = {
        "to": Web3.to_checksum_address(payee), "value": clearing_price_micro, "data": bytes.fromhex(run_id[2:]),
        # "pending": the simulator's own broadcast from this key may still be in the mempool
        "nonce": w3.eth.get_transaction_count(acct.address, "pending"), "chainId": w3.eth.chain_id,
        "maxFeePerGas": w3.eth.gas_price * 2, "maxPriorityFeePerGas": w3.eth.gas_price,
    }
    tx["gas"] = int(w3.eth.estimate_gas({**tx, "from": acct.address}) * 1.2)
    h = w3.eth.send_raw_transaction(acct.sign_transaction(tx).raw_transaction)
    rcpt = w3.eth.wait_for_transaction_receipt(h, timeout=180)
    return {"tx": h.hex() if str(h.hex()).startswith("0x") else "0x" + h.hex(), "from": acct.address, "to": payee,
            "value_wei": clearing_price_micro, "block": rcpt.blockNumber, "status": rcpt.status}


# ─── The driver ────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--buyer-max", type=float, required=True, help="Agent A's reserve (stays local + Vault only)")
    ap.add_argument("--seller-min", type=float, required=True, help="Agent B's reserve (stays local + Vault only)")
    ap.add_argument("--agent-a", default="sealedbid-agent-a")
    ap.add_argument("--agent-b", default="sealedbid-agent-b")
    ap.add_argument("--agent-b-address", default=os.environ.get("AGENT_B_ADDRESS", "0xaE4B357dBf9127b17050a0128Dc146fa31bE89c1"))
    ap.add_argument("--target", default="simulation-settings", help="CRE target; simulation-settings writes to the simulation receiver")
    ap.add_argument("--broadcast", action="store_true", help="really write the SETTLE to Base Sepolia through the simulator")
    ap.add_argument("--settle-transfer", action="store_true", help="on SETTLE, Agent A pays Agent B on Base Sepolia (testnet)")
    ap.add_argument("--attest", action="store_true", help="buy the $0.25 process-followed attestation over the evidence chain")
    ap.add_argument("--no-pay", action="store_true", help="skip every BlindOracle call (free run)")
    ap.add_argument("--high-value-usd", type=float, default=100.0, help="pre-hire check above this trade value ($0.25)")
    args = ap.parse_args()

    bc._load_env()
    reserves = [args.buyer_max, args.seller_min]
    run_label = f"demo-{int(time.time())}"
    salt_a, salt_b = secrets.token_hex(6), secrets.token_hex(6)
    chain = EvidenceChain(os.environ.get("EVIDENCE_HMAC_SECRET") or secrets.token_hex(16), run_label)
    client = None if args.no_pay else bc.BoClient()
    report: dict[str, Any] = {"run_label": run_label, "started": now_iso(), "agent_a": args.agent_a, "agent_b": args.agent_b, "steps": {}}
    print(f"sealed-bid demo · {args.agent_a} (buyer) ↔ {args.agent_b} (seller) · label {run_label}")
    # Announce the scheme rather than leaving it to be inferred from the records. The
    # two schemes give materially different guarantees (RAP-1 §7.3) and a reader must
    # not have to dig into `sig_scheme` to find out which one this run actually used.
    if chain.scheme == "ed25519":
        print(f"  {'evidence':15} ed25519, attributable · pubkey {chain.key[:16]}… "
              f"(published: evidence/signing-key.pub)")
    else:
        print(f"  {'evidence':15} hmac-sha256, tamper-evident only — no "
              f"EVIDENCE_ED25519_PRIVATE_KEY set, so this run attributes nothing")

    def paid(step: str, fn, *a, **kw):
        if client is None:
            print(f"  {step:15} skipped (--no-pay)")
            return None
        r = fn(*a, **kw)
        d = r.deliverable if isinstance(r.deliverable, dict) else {}
        print(f"  {step:15} {r.sku} ${r.price_usd} → HTTP {r.status} tx={r.tx_hash}")
        return {"sku": r.sku, "price_usd": r.price_usd, "status": r.status, "tx_hash": r.tx_hash, "job_id": r.extra.get("job_id"),
                "deliverable_brief": {k: d.get(k) for k in ("found", "score", "badge", "badge_label", "verdict", "red_flags") if k in d}}

    # 1–2. each party vets the other before bidding
    trade_value_guess = (args.buyer_max + args.seller_min) / 2  # only used for the threshold; never written
    for step, me, other in (("vet_a", args.agent_a, args.agent_b), ("vet_b", args.agent_b, args.agent_a)):
        rep = paid(step, getattr(client, "reputation_lookup", None), other, changed_outcome=False, note=f"{step}: {me} vets {other}")
        pre = None
        if trade_value_guess >= args.high_value_usd:
            pre = paid(step + "/prehire", getattr(client, "prehire_check", None), other, run_label, changed_outcome=False)
        report["steps"][step] = {"reputation": rep, "prehire": pre}
        chain.append(step, actor=me, counterparty=other, reputation_tx=(rep or {}).get("tx_hash"), prehire_tx=(pre or {}).get("tx_hash"),
                     prehire_verdict=((pre or {}).get("deliverable_brief") or {}).get("verdict"))

    # 3. bracket open — the buyer's badge, tagged with the run label (the run id is not known until the enclave runs)
    badge = paid("bracket_open", getattr(client, "trust_badge", None), args.agent_a, run_label, note="bracket open")
    report["steps"]["bracket_open"] = badge
    chain.append("bracket_open", actor=args.agent_a, badge_tx=(badge or {}).get("tx_hash"))

    # 4. seal — the only place the reserves go: into the simulator's enclave as Vault secrets
    print(f"  {'seal':15} cre workflow simulate --target={args.target}{' --broadcast' if args.broadcast else ''} …")
    sim_out, sim = run_enclave(args.buyer_max, args.seller_min, salt_a, salt_b, args.broadcast, args.target)
    (DEMO_EVIDENCE).mkdir(parents=True, exist_ok=True)
    print(f"  {'':15} enclave says: {sim['result_line']!r}  runId={sim['run_id']}")
    if sim["outcome"] is None:
        print(sim_out[-3000:])
        print("enclave run failed — see output above")
        return 1
    report["seal"] = sim
    chain.append("seal", outcome=sim["outcome"], run_id=sim["run_id"], commitment_a=sim["commitment_a"], commitment_b=sim["commitment_b"],
                 clearing_price=sim["clearing_price"], settlement_tx=sim["settlement_tx"], broadcast=args.broadcast)

    # 5. settle or abort
    settle: dict[str, Any] = {"outcome": sim["outcome"]}
    if sim["outcome"] == "SETTLE":
        if args.broadcast and sim["settlement_tx"]:
            onchain = read_receiver(sim["run_id"], sim["receiver"] or SIM_RECEIVER, sim["settlement_tx"])
            match = onchain["clearing_price_micro"] == round(sim["clearing_price"] * PRICE_SCALE)
            print(f"  {'settle':15} receiver {onchain['receiver'][:10]}… holds clearing={onchain['clearing_price_micro']} micro "
                  f"({'matches' if match else 'MISMATCH'}) recordedAt={onchain['recorded_at']} count={onchain['settlement_count']}")
            settle["receiver_read"] = onchain
            settle["receiver_matches_enclave"] = match
        if args.settle_transfer:
            key = os.environ.get("AGENT_A_PRIVATE_KEY") or os.environ.get("CRE_ETH_PRIVATE_KEY")
            if not key:
                print("  settle-transfer skipped: no AGENT_A_PRIVATE_KEY")
            else:
                t = settle_transfer(round(sim["clearing_price"] * PRICE_SCALE), sim["run_id"], key, args.agent_b_address)
                print(f"  {'transfer':15} A → B {t['value_wei']} wei on Base Sepolia, tx={t['tx']} block={t['block']} status={t['status']}")
                settle["transfer"] = t
    else:
        print(f"  {'abort':15} {sim['outcome']}: nothing written on chain, nothing transferred")
    report["settle_or_abort"] = settle
    chain.append("settle_or_abort", **{k: v for k, v in settle.items() if k != "receiver_read"},
                 receiver_clearing_micro=(settle.get("receiver_read") or {}).get("clearing_price_micro"))

    # 6. (optional) process-followed attestation over the signed chain
    if args.attest and client:
        declared = {"ordered": True, "required": [{"id": s} for s in STEP_IDS if s != "bracket_close"]}
        payload = {"declared_process": declared, "run_evidence": chain.records}
        bc.assert_no_reserve(payload, reserves)  # the load-bearing check before anything leaves
        att = client.process_attestation(declared, chain.records)
        d = att.deliverable if isinstance(att.deliverable, dict) else {}
        print(f"  {'attest':15} {att.sku} ${att.price_usd} → HTTP {att.status} verdict={d.get('verdict')} tx={att.tx_hash}")
        report["attestation"] = {"status": att.status, "tx_hash": att.tx_hash, "job_id": att.extra.get("job_id"),
                                 "verdict": d.get("verdict"), "checks": d.get("checks") or d.get("assertions") or d.get("results")}
        chain.append("attest", attestation_tx=att.tx_hash, verdict=d.get("verdict"))

    # 7. bracket close
    close = paid("bracket_close", getattr(client, "reputation_lookup", None), args.agent_b, changed_outcome=False, note="bracket close")
    report["steps"]["bracket_close"] = close
    chain.append("bracket_close", actor=args.agent_a, counterparty=args.agent_b, reputation_tx=(close or {}).get("tx_hash"))

    # persist — after proving the reserves are nowhere in it
    report["evidence_chain"] = chain.records
    report["evidence_chain_verifies"] = EvidenceChain.verify(chain.records)
    report["finished"] = now_iso()
    bc.assert_no_reserve(report, reserves)
    out = DEMO_EVIDENCE / f"{sim['run_id']}.json"
    out.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    print(f"\nresult: {sim['result_line']}   evidence: {out.relative_to(REPO)}   chain verifies: {report['evidence_chain_verifies']}")
    print("reserves in the evidence file: none (checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
