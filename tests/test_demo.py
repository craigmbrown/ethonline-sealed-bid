"""Offline tests for scripts/demo.py: simulator-output parsing, the evidence chain, and the
reserve rule on everything the demo persists. No network, no chain, no payment."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import demo

import bo_client as bc

SIM_SETTLE = """
2026-09-05T14:41:27Z [USER LOG] enclave: outcome=SETTLE runId=0xc5c693bb8edefd9653add5c1627425fa472221b74daacfdaf59d814aba415b00
2026-09-05T14:41:27Z [USER LOG] enclave: commitmentA=0x3f8d9f965b0ac8f16db167d6489f8732bedfe9cc40f551ecccab05fb23737319 commitmentB=0x73786c9229a0b79ed9b92ffa8eecc51b7f0f218294cb54996dc50e8ab04dbf0e
2026-09-05T14:41:29Z [USER LOG] settlement: written to 0xA2eB7d6EEd6a4d0976cb29B226093E007E720590 tx=0x6a02d0ca2ab8bee53f2745047ffdfce8c1ee45f0cac35d1bd7d37a9196c834c9
✓ Workflow Simulation Result:
"SETTLE @ 105 (run: simulation) tx: 0x6a02d0ca2ab8bee53f2745047ffdfce8c1ee45f0cac35d1bd7d37a9196c834c9"
2026-09-05T14:41:29Z [SIMULATION] Execution finished signal received
"""
SIM_NO_OVERLAP = """
2026-09-05T14:41:37Z [USER LOG] enclave: outcome=NO_OVERLAP runId=0x84d1238f8f506e4bbcf10a66ec94c9f432db81a33140587dd7cb9665e5e8f76b
2026-09-05T14:41:37Z [USER LOG] enclave: commitmentA=0x66370ae1042a0c38387e724cb607bc2400acc3f065e0fe518b4b74b4cd3a58ec commitmentB=0x7c8e861e4ad020b73632855f19169cacc990bb50c8ce293d58ca16187792fb6d
✓ Workflow Simulation Result:
"NO_OVERLAP (run: simulation)"
"""


def test_parse_settle_output():
    p = demo.parse_simulation(SIM_SETTLE)
    assert p["outcome"] == "SETTLE" and p["clearing_price"] == 105.0
    assert p["run_id"].startswith("0xc5c693bb") and p["receiver"] == "0xA2eB7d6EEd6a4d0976cb29B226093E007E720590"
    assert p["settlement_tx"].startswith("0x6a02d0ca") and p["commitment_a"].startswith("0x3f8d")


def test_parse_no_overlap_output_has_no_price_and_no_tx():
    p = demo.parse_simulation(SIM_NO_OVERLAP)
    assert p["outcome"] == "NO_OVERLAP" and p["clearing_price"] is None and p["settlement_tx"] is None


def test_parse_garbage_is_none():
    p = demo.parse_simulation("nothing useful here")
    assert p["outcome"] is None and p["run_id"] is None


def test_evidence_chain_verifies_and_detects_tampering():
    ch = demo.EvidenceChain("secret", "run-1")
    ch.append("vet_a", actor="a", counterparty="b")
    ch.append("seal", outcome="SETTLE", clearing_price=105.0)
    ch.append("settle_or_abort", outcome="SETTLE")
    assert demo.EvidenceChain.verify(ch.records)
    tampered = json.loads(json.dumps(ch.records))
    tampered[1]["clearing_price"] = 104.0  # edit a signed field
    assert not demo.EvidenceChain.verify(tampered)
    reordered = [ch.records[0], ch.records[2], ch.records[1]]
    assert not demo.EvidenceChain.verify(reordered)
    dropped = ch.records[:1] + ch.records[2:]
    assert not demo.EvidenceChain.verify(dropped)


def test_chain_records_carry_the_step_shape_the_attestation_expects():
    ch = demo.EvidenceChain("s", "r")
    rec = ch.append("bracket_open", actor="a")
    for k in ("step_id", "ts", "prev_sha256", "hmac", "pubkey", "sig_scheme"):
        assert k in rec


def test_step_ids_match_spec_declared_process():
    assert demo.STEP_IDS == ["vet_a", "vet_b", "bracket_open", "seal", "settle_or_abort", "bracket_close"]


def test_evidence_with_clearing_price_but_no_reserve_passes_the_rule():
    ch = demo.EvidenceChain("s", "r")
    ch.append("seal", outcome="SETTLE", clearing_price=105.0, run_id="0x" + "c5" * 32)
    bc.assert_no_reserve({"run_evidence": ch.records}, [120.0, 90.0])  # must not raise


def test_evidence_that_leaks_a_reserve_is_refused():
    ch = demo.EvidenceChain("s", "r")
    ch.append("seal", outcome="SETTLE", clearing_price=105.0, note="buyer max was 120")
    with pytest.raises(bc.ReserveLeak):
        bc.assert_no_reserve({"run_evidence": ch.records}, [120.0, 90.0])
