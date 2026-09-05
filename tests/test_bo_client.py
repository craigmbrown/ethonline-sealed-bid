"""Offline tests for bo_client. No network, no payment: the HTTP session is faked."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bo_client as bc

BUYER_MAX = 120.0
SELLER_MIN = 90.0
ATTESTATION = {
    "result": "SETTLE",
    "clearingPriceMicro": 105_000_000,
    "runLabel": "simulation",
    "commitmentA": "0x3f8d9f965b0ac8f16db167d6489f8732bedfe9cc40f551ecccab05fb23737319",
    "commitmentB": "0x73786c9229a0b79ed9b92ffa8eecc51b7f0f218294cb54996dc50e8ab04dbf0e",
    "runId": "0xc5c693bb8edefd9653add5c1627425fa472221b74daacfdaf59d814aba415b00",
}
TX = "0x6a02d0ca2ab8bee53f2745047ffdfce8c1ee45f0cac35d1bd7d37a9196c834c9"


class FakeResp:
    def __init__(self, status: int, body):
        self.status_code = status
        self._body = body
        self.content = b"x"
        self.text = json.dumps(body)
        self.headers = {}

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeSession:
    """Records every request; answers paid routes with a settled envelope."""

    def __init__(self):
        self.posts: list[tuple[str, dict]] = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.posts.append((url, json))
        sku = url.rsplit("/", 1)[-1]
        # Mirrors the live envelope observed 2026-09-05 (see EVIDENCE.md).
        return FakeResp(
            200,
            {
                "sku_id": sku,
                "job_id": f"job-{sku[:4]}",
                "status": "complete",
                "deliverable": {"found": True, "score": 0, "badge": "none"},
                "powered_by": "BlindOracle",
                "payment": {"rail": "base_usdc_x402", "tx_hash": TX, "network": "eip155:8453", "payer": "0x" + "2d" * 20},
                "bo_trust": {"content_sha256": "ab" * 32, "powered_by": "BlindOracle"},
            },
        )

    def get(self, url, timeout=None):
        return FakeResp(200, {"services": [{"sku_id": s, "price_usd": p} for s, p in bc.CATALOG_PRICE_USD.items()]})


@pytest.fixture
def client(tmp_path):
    return bc.BoClient(
        base_url="https://example.invalid", session=FakeSession(), call_log=tmp_path / "calls.jsonl", evidence_dir=tmp_path / "ev"
    )


# ─── the load-bearing tests: the evidence rule (SPEC §2.4) ─────────────────


def test_dispute_evidence_never_contains_a_reserve():
    ev = bc.build_dispute_evidence(ATTESTATION, TX)
    bc.assert_no_reserve(ev, [BUYER_MAX, SELLER_MIN])  # must not raise
    nums = bc._numbers_in(ev)
    assert BUYER_MAX not in nums and SELLER_MIN not in nums
    assert 105_000_000.0 in nums  # the clearing price IS allowed — it is public on chain
    assert all(e.startswith(("enclave_attestation:", "settlement_tx:")) for e in ev)


def test_assert_no_reserve_catches_a_leak_in_a_string_and_in_a_number():
    with pytest.raises(bc.ReserveLeak):
        bc.assert_no_reserve(["my reserve was 120"], [BUYER_MAX, SELLER_MIN])
    with pytest.raises(bc.ReserveLeak):
        bc.assert_no_reserve({"claim": "x", "limit": 90}, [BUYER_MAX, SELLER_MIN])
    with pytest.raises(bc.ReserveLeak):
        bc.assert_no_reserve({"nested": [{"deep": "seller min 90.0"}]}, [BUYER_MAX, SELLER_MIN])


def test_assert_no_reserve_ignores_digits_inside_hex_hashes():
    # runId contains "120"-like digit runs inside hex; those are not reserves.
    bc.assert_no_reserve([ATTESTATION["runId"], ATTESTATION["commitmentA"]], [120.0, 90.0, 5.0, 12.0])


def test_dispute_refuses_without_explicit_real_money_optin(client):
    with pytest.raises(PermissionError):
        client.dispute("claim", ["e"], "a", "b", reserves_never_to_reveal=[BUYER_MAX, SELLER_MIN])


def test_dispute_refuses_a_payload_that_leaks_a_reserve_even_with_optin(client):
    with pytest.raises(bc.ReserveLeak):
        client.dispute(
            "seller reneged; my max was 120",
            bc.build_dispute_evidence(ATTESTATION, TX),
            "agent-a",
            "agent-b",
            reserves_never_to_reveal=[BUYER_MAX, SELLER_MIN],
            confirm_real_money_usd_5=True,
        )
    assert client.session.posts == []  # nothing was sent


def test_dispute_sends_attestation_only_evidence_when_clean(client):
    res = client.dispute(
        "seller did not deliver after SETTLE",
        bc.build_dispute_evidence(ATTESTATION, TX),
        "agent-a",
        "agent-b",
        reserves_never_to_reveal=[BUYER_MAX, SELLER_MIN],
        confirm_real_money_usd_5=True,
    )
    assert res.ok
    url, body = client.session.posts[-1]
    assert url.endswith(bc.SKU["dispute"])
    bc.assert_no_reserve(body, [BUYER_MAX, SELLER_MIN])
    assert any("enclave_attestation:" in e for e in body["evidence"])


# ─── plumbing ──────────────────────────────────────────────────────────────


def test_reputation_lookup_shape_and_value_log(client, tmp_path):
    res = client.reputation_lookup("agent-b", changed_outcome=False, note="pre-bid vet")
    url, body = client.session.posts[-1]
    assert url.endswith("/v1/services/reputation.lookup")
    assert body == {"task": "agent_id: agent-b"}
    assert res.ok and res.tx_hash == TX and res.price_usd == 0.01 and res.content_sha256 == "ab" * 32
    rows = [json.loads(l) for l in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert rows[-1]["sku"] == "reputation.lookup"
    assert rows[-1]["tx_hash"] == TX
    assert rows[-1]["changed_outcome"] is False
    assert "bo-value sku=reputation.lookup price_usd=0.01" in res.summary()


def test_badge_and_prehire_bodies(client):
    client.trust_badge("agent-a", ATTESTATION["runId"])
    _, body = client.session.posts[-1]
    assert body == {"agent_name": "agent-a", "task_id": ATTESTATION["runId"]}
    client.prehire_check("agent-b", ATTESTATION["runId"], wallet="0x" + "11" * 20)
    _, body = client.session.posts[-1]
    assert body["wallet"].startswith("0x") and body["agent_name"] == "agent-b"


def test_vet_counterparty_runs_prehire_only_above_threshold(client):
    low = client.vet_counterparty("agent-b", "t1", trade_value_usd=10, high_value_threshold_usd=100)
    assert low["prehire"] is None and low["reputation"].ok
    high = client.vet_counterparty("agent-b", "t2", trade_value_usd=250, high_value_threshold_usd=100)
    assert high["prehire"] is not None and high["prehire"].sku == bc.SKU["prehire"]


def test_parse_envelope_is_defensive():
    r = bc.parse_envelope("reputation.lookup", 500, "not json", 0.1)
    assert not r.ok and r.tx_hash is None and r.price_usd == 0.01
    r = bc.parse_envelope("agent.trust-badge", 200, {"deliverable": {"badge": "none"}}, 0.1)
    assert r.ok and r.deliverable == {"badge": "none"}


def test_prices_match_spec_table():
    assert bc.CATALOG_PRICE_USD[bc.SKU["reputation"]] == 0.01
    assert bc.CATALOG_PRICE_USD[bc.SKU["badge"]] == 0.01
    assert bc.CATALOG_PRICE_USD[bc.SKU["prehire"]] == 0.25
    assert bc.CATALOG_PRICE_USD[bc.SKU["process"]] == 0.25
    assert bc.CATALOG_PRICE_USD[bc.SKU["dispute"]] == 5.00
