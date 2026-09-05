#!/usr/bin/env python3
"""bo_client — thin client for the public BlindOracle API (SPEC.md §2.4).

BlindOracle is a pay-per-call HTTP API at https://api.craigmbrown.com. Every paid
route answers HTTP 402 with an x402 `exact` challenge (USDC on Base mainnet,
EIP-3009 `TransferWithAuthorization`, settled gaslessly by the facilitator). This
module signs those challenges with the `x402` SDK from PyPI and exposes the six
calls the sealed-bid protocol makes:

    vet (pre-bid)      reputation_lookup(counterparty)            $0.01
    vet (high value)   prehire_check(counterparty)                $0.25
    bracket open       trust_badge(self, task_id=run_id)          $0.01
    post-settle        process_attestation(declared, evidence)    $0.25
    contested          dispute(...)                               $5.00  (explicit opt-in)
    bracket close      reputation_lookup(counterparty)            $0.01

Every paid call appends one JSON line to `bo_calls.jsonl`:
    {ts, sku, price_usd, result, proof_id, tx_hash, changed_outcome, ...}
and prints the same as `bo-value sku=... price_usd=... result=... proof_id=... changed_outcome=...`.

The evidence rule (SPEC §2.4, load-bearing): a party's reserve price must NEVER be
submitted to the arbiter. `build_dispute_evidence()` only ever emits the enclave
attestation fields (result, clearing price, commitments, run id) and
`assert_no_reserve()` refuses any payload that carries a reserve as a number.

Secrets come from the process env or a gitignored `.env` beside this file:
    BLINDORACLE_API_BASE           default https://api.craigmbrown.com
    BLINDORACLE_PAYER_PRIVATE_KEY  0x… key of the wallet that pays (USDC on Base)
                                   fallback: AGENT_A_PRIVATE_KEY, then CRE_ETH_PRIVATE_KEY
    BLINDORACLE_AGENT_A_API_KEY    optional Bearer key from /v1/agents/register
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

REPO = Path(__file__).resolve().parent
DEFAULT_BASE = "https://api.craigmbrown.com"
CALL_LOG = REPO / "bo_calls.jsonl"
EVIDENCE_DIR = REPO / "evidence" / "bo"

SKU = {
    "reputation": "reputation.lookup",
    "badge": "agent.trust-badge",
    "prehire": "agent.prehire-check",
    "process": "security.process-attestation",
    "dispute": "arbitration.dispute-settlement",
}
# Catalog prices as read on 2026-09-05; the live 402 challenge is authoritative.
CATALOG_PRICE_USD = {
    "reputation.lookup": 0.01,
    "agent.trust-badge": 0.01,
    "agent.prehire-check": 0.25,
    "security.process-attestation": 0.25,
    "arbitration.dispute-settlement": 5.00,
}
USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"


def _load_env() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv(REPO / ".env", override=False)
    except ImportError:
        pass


# ─── Result envelope ───────────────────────────────────────────────────────


@dataclass
class BoResult:
    sku: str
    status: int
    body: dict[str, Any]
    price_usd: float | None = None
    tx_hash: str | None = None
    proof_id: str | None = None
    content_sha256: str | None = None
    elapsed_s: float = 0.0
    changed_outcome: bool | None = None
    note: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def deliverable(self) -> Any:
        return self.body.get("deliverable", self.body)

    def summary(self) -> str:
        return (
            f"bo-value sku={self.sku} price_usd={self.price_usd} status={self.status} "
            f"result={'ok' if self.ok else 'fail'} proof_id={self.proof_id} tx={self.tx_hash} "
            f"changed_outcome={self.changed_outcome} {self.note}".rstrip()
        )


def parse_envelope(sku: str, status: int, body: Any, elapsed_s: float) -> BoResult:
    """Pull the settlement + trust-envelope fields out of a paid response, defensively."""
    b = body if isinstance(body, dict) else {"raw": body}
    # Live shape (2026-09-05): {"sku_id", "job_id", "status", "deliverable", "powered_by",
    #   "payment": {"rail": "base_usdc_x402", "tx_hash", "network", "payer", "verify"}, "bo_trust": {...}}
    pay = b.get("payment") or b.get("payment_proof") or b.get("settlement") or {}
    trust = b.get("bo_trust") or {}
    tx = pay.get("tx_hash") or pay.get("settlement_ref") or b.get("tx_hash")
    price = pay.get("amount_usd") or pay.get("price_usd") or b.get("price_usd")
    try:
        price = float(price) if price is not None else None
    except (TypeError, ValueError):
        price = None
    return BoResult(
        sku=sku,
        status=status,
        body=b,
        price_usd=price if price is not None else CATALOG_PRICE_USD.get(sku),
        tx_hash=tx,
        proof_id=pay.get("proof_id") or b.get("proof_id") or trust.get("proof_id") or b.get("job_id"),
        content_sha256=trust.get("content_sha256"),
        elapsed_s=elapsed_s,
        extra={"rail": pay.get("rail"), "payer": pay.get("payer"), "verify": pay.get("verify"), "job_id": b.get("job_id")},
    )


# ─── Evidence rule (SPEC §2.4) ─────────────────────────────────────────────

_NUM = re.compile(r"(?<![0-9a-fA-Fx])\d+(?:\.\d+)?(?![0-9a-fA-F])")


def _numbers_in(obj: Any) -> set[float]:
    """Every numeric token in a JSON-able object, hex words excluded (hashes contain digits)."""
    found: set[float] = set()

    def walk(o: Any) -> None:
        if isinstance(o, bool):
            return
        if isinstance(o, (int, float)):
            found.add(float(o))
        elif isinstance(o, str):
            for tok in _NUM.findall(re.sub(r"0x[0-9a-fA-F]+", " ", o)):
                found.add(float(tok))
        elif isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, (list, tuple)):
            for v in o:
                walk(v)

    walk(obj)
    return found


class ReserveLeak(ValueError):
    pass


def assert_no_reserve(payload: Any, reserves: list[float]) -> None:
    """Refuse a payload that carries a party's reserve price as a number.

    The clearing price is allowed (it is public on chain); a reserve is not — the
    whole point of the protocol is that the arbiter never learns it.
    """
    present = _numbers_in(payload)
    for r in reserves:
        if float(r) in present:
            raise ReserveLeak("a reserve price is present in a payload that leaves the parties")


def build_dispute_evidence(attestation: dict[str, Any], settlement_tx: str | None = None) -> list[str]:
    """Evidence strings for the arbiter: the enclave attestation only, never an input.

    `attestation` is the on-chain report as decoded from the receiver:
      result, clearingPriceMicro, runLabel, commitmentA, commitmentB, runId
    """
    allowed = ("result", "clearingPriceMicro", "runLabel", "commitmentA", "commitmentB", "runId")
    att = {k: str(attestation[k]) for k in allowed if k in attestation}
    ev = [f"enclave_attestation:{json.dumps(att, sort_keys=True, separators=(',', ':'))}"]
    if settlement_tx:
        ev.append(f"settlement_tx:{settlement_tx}")
    return ev


# ─── Client ────────────────────────────────────────────────────────────────


class BoClient:
    def __init__(
        self,
        base_url: str | None = None,
        payer_private_key: str | None = None,
        api_key: str | None = None,
        session: requests.Session | None = None,
        call_log: Path | None = CALL_LOG,
        evidence_dir: Path | None = EVIDENCE_DIR,
        timeout: float = 60.0,
    ) -> None:
        _load_env()
        self.base = (base_url or os.environ.get("BLINDORACLE_API_BASE", DEFAULT_BASE)).rstrip("/")
        self.api_key = api_key or os.environ.get("BLINDORACLE_AGENT_A_API_KEY") or None
        self.call_log = call_log
        self.evidence_dir = evidence_dir
        self.timeout = timeout
        self.payer_address: str | None = None
        key = (
            payer_private_key
            or os.environ.get("BLINDORACLE_PAYER_PRIVATE_KEY")
            or os.environ.get("AGENT_A_PRIVATE_KEY")
            or os.environ.get("CRE_ETH_PRIVATE_KEY")
        )
        if session is not None:
            self.session = session
        elif key and key.startswith("0x") and len(key) == 66:
            self.session = self._paying_session(key)
        else:
            self.session = requests.Session()  # free routes only; paid routes will 402

    def _paying_session(self, key: str) -> requests.Session:
        from eth_account import Account
        from x402 import x402ClientSync
        from x402.http.clients.requests import x402_requests
        from x402.mechanisms.evm import EthAccountSigner
        from x402.mechanisms.evm.exact import ExactEvmScheme

        account = Account.from_key(key)
        self.payer_address = account.address
        client = x402ClientSync()
        client.register("eip155:8453", ExactEvmScheme(EthAccountSigner(account)))
        return x402_requests(client)

    # ── plumbing ──

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json", "User-Agent": "sealed-bid-agent-settlement/0.1"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _post_sku(self, sku: str, body: dict[str, Any], changed_outcome: bool | None, note: str = "") -> BoResult:
        t0 = time.time()
        r = self.session.post(f"{self.base}/v1/services/{sku}", json=body, headers=self._headers(), timeout=self.timeout)
        try:
            parsed = r.json()
        except ValueError:
            parsed = {"raw": r.text[:2000]}
        res = parse_envelope(sku, r.status_code, parsed, time.time() - t0)
        res.changed_outcome = changed_outcome
        res.note = note
        self._record(res, body)
        return res

    def _record(self, res: BoResult, request_body: dict[str, Any]) -> None:
        row = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "sku": res.sku,
            "price_usd": res.price_usd,
            "status": res.status,
            "result": "ok" if res.ok else "fail",
            "proof_id": res.proof_id,
            "tx_hash": res.tx_hash,
            "content_sha256": res.content_sha256,
            "changed_outcome": res.changed_outcome,
            "payer": self.payer_address,
            "request_keys": sorted(request_body.keys()),
            "elapsed_s": round(res.elapsed_s, 2),
            "note": res.note,
        }
        print(res.summary(), file=sys.stderr)
        if self.call_log:
            with open(self.call_log, "a") as f:
                f.write(json.dumps(row, sort_keys=True) + "\n")
            # Persist the deliverable + payment block as submission evidence (no request
            # body, no headers, no keys). One file per paid call, named by sku + job id.
            if res.ok and self.evidence_dir:
                self.evidence_dir.mkdir(parents=True, exist_ok=True)
                name = f"{row['ts'].replace(':', '').replace('-', '')}-{res.sku}-{res.extra.get('job_id') or 'nojob'}.json"
                keep = {k: res.body.get(k) for k in ("sku_id", "job_id", "status", "deliverable", "payment", "bo_trust", "powered_by") if k in res.body}
                (self.evidence_dir / name).write_text(json.dumps(keep, indent=1, sort_keys=True) + "\n")

    # ── free routes ──

    def catalog(self) -> dict[str, Any]:
        r = self.session.get(f"{self.base}/v1/services", timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def register(self, name: str, capabilities: list[str], evm_address: str | None = None) -> dict[str, Any]:
        """Self-serve passport (observer tier). The api_key is returned ONCE — store it."""
        body: dict[str, Any] = {"name": name, "capabilities": capabilities}
        if evm_address:
            body["evm_address"] = evm_address
        r = self.session.post(f"{self.base}/v1/agents/register", json=body, headers=self._headers(), timeout=self.timeout)
        return {"status": r.status_code, **(r.json() if r.content else {})}

    def settlement_proof(self, ref: str) -> dict[str, Any]:
        r = self.session.get(f"{self.base}/v1/proofs/settlement/{ref}", timeout=self.timeout)
        return {"status": r.status_code, **(r.json() if r.content else {})}

    # ── paid routes (SPEC §2.4 table) ──

    def reputation_lookup(self, agent: str, changed_outcome: bool | None = None, note: str = "") -> BoResult:
        return self._post_sku(SKU["reputation"], {"task": f"agent_id: {agent}"}, changed_outcome, note)

    def trust_badge(self, agent_name: str, task_id: str, wallet: str | None = None, note: str = "") -> BoResult:
        body: dict[str, Any] = {"agent_name": agent_name, "task_id": task_id}
        if wallet:
            body["wallet"] = wallet
        return self._post_sku(SKU["badge"], body, None, note)

    def prehire_check(self, agent_name: str, task_id: str, wallet: str | None = None, changed_outcome: bool | None = None) -> BoResult:
        body: dict[str, Any] = {"agent_name": agent_name, "task_id": task_id}
        if wallet:
            body["wallet"] = wallet
        return self._post_sku(SKU["prehire"], body, changed_outcome)

    def process_attestation(self, declared_process: dict[str, Any], run_evidence: list[dict[str, Any]]) -> BoResult:
        return self._post_sku(SKU["process"], {"declared_process": declared_process, "run_evidence": run_evidence}, None)

    def dispute(
        self,
        claim: str,
        evidence: list[str],
        claimant: str,
        respondent: str,
        reserves_never_to_reveal: list[float],
        requested_remedy: str = "",
        disputed_job_id: str | None = None,
        confirm_real_money_usd_5: bool = False,
    ) -> BoResult:
        """$5.00 on Base mainnet. Requires an explicit opt-in AND passes the evidence rule."""
        if not confirm_real_money_usd_5:
            raise PermissionError("dispute() costs 5 USDC on Base mainnet; pass confirm_real_money_usd_5=True")
        body: dict[str, Any] = {"claim": claim, "evidence": evidence, "claimant": claimant, "respondent": respondent}
        if requested_remedy:
            body["requested_remedy"] = requested_remedy
        if disputed_job_id:
            body["disputed_job_id"] = disputed_job_id
        assert_no_reserve(body, reserves_never_to_reveal)
        return self._post_sku(SKU["dispute"], body, None)

    # ── protocol helpers ──

    def vet_counterparty(self, agent: str, task_id: str, trade_value_usd: float, high_value_threshold_usd: float = 100.0) -> dict[str, Any]:
        """Pre-bid vetting: reputation always; pre-hire check above the value threshold."""
        rep = self.reputation_lookup(agent, note="pre-bid vet")
        out: dict[str, Any] = {"reputation": rep, "prehire": None}
        if trade_value_usd >= high_value_threshold_usd:
            out["prehire"] = self.prehire_check(agent, task_id)
        return out


# ─── CLI ───────────────────────────────────────────────────────────────────


def _main(argv: list[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="BlindOracle client for the sealed-bid protocol")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("catalog", help="print the SKUs this client uses with live prices (free)")
    p = sub.add_parser("reputation", help="$0.01 reputation lookup")
    p.add_argument("agent")
    p = sub.add_parser("badge", help="$0.01 trust badge (bracket open/close)")
    p.add_argument("agent_name")
    p.add_argument("task_id")
    p = sub.add_parser("prehire", help="$0.25 pre-hire check")
    p.add_argument("agent_name")
    p.add_argument("task_id")
    p = sub.add_parser("proof", help="free settlement-proof lookup by tx hash or ref")
    p.add_argument("ref")
    p = sub.add_parser("register", help="free self-serve passport; prints everything except the api_key")
    p.add_argument("name")
    p.add_argument("--capability", action="append", default=["sealed-bid-counterparty"])
    p.add_argument("--evm-address")
    args = ap.parse_args(argv)

    c = BoClient()
    if args.cmd == "catalog":
        cat = c.catalog()
        want = set(SKU.values())
        for s in cat.get("services", []):
            if s.get("sku_id") in want:
                print(f"{s['sku_id']:38} ${s['price_usd']:<6} {s['name']}")
        return 0
    if args.cmd == "reputation":
        res = c.reputation_lookup(args.agent)
    elif args.cmd == "badge":
        res = c.trust_badge(args.agent_name, args.task_id)
    elif args.cmd == "prehire":
        res = c.prehire_check(args.agent_name, args.task_id)
    elif args.cmd == "proof":
        print(json.dumps(c.settlement_proof(args.ref), indent=1))
        return 0
    elif args.cmd == "register":
        out = c.register(args.name, args.capability, args.evm_address)
        out.pop("api_key", None)
        print(json.dumps(out, indent=1))
        return 0
    else:
        return 2
    print(json.dumps(res.body, indent=1)[:4000])
    return 0 if res.ok else 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
