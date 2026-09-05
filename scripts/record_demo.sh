#!/usr/bin/env bash
# Runs the demo walkthrough (docs/VIDEO-SCRIPT.md) as a real terminal session, suitable for
# `asciinema rec -c "scripts/record_demo.sh" evidence/demo.cast`. Every command is real; the
# free path is used (no paid BlindOracle calls) so anyone can reproduce it with only a little
# Base Sepolia ETH. Set PAID=1 to run the SETTLE case with the paid bracket instead (~$0.79).
#
# Marker lines (`### t=NN title`) are printed for the caption renderer; they carry no secrets.
set -uo pipefail
cd "$(dirname "$0")/.."
export FORCE_COLOR=1
PAID="${PAID:-0}"

say()  { printf '\n### %s\n' "$*"; }
type_() { printf '\033[1;32m$\033[0m %s\n' "$*"; sleep 1.2; }
run()  { type_ "$*"; bash -c "$*"; sleep 1.5; }

say "t=0 Sealed-Bid Agent Settlement — two agents, two private reserves, one enclave"
sleep 2
say "t=1 The enclave handler: cre.handlerInTee, ONE getSecrets call, overlap maths, salted commitments"
run "sed -n '/^export const sealBids/,/^}/p;/^export const runSealedBid/,/getSecrets/p' sealed-bid-ts/workflow.ts | head -30"
sleep 2
say "t=2 The security property is a test suite — with deliberately leaky seals as negative controls"
run "cd sealed-bid-ts && bun test 2>&1 | tail -6"
run "cd contracts && forge test 2>&1 | tail -4"
say "t=3 The whole protocol, for real: vet, bracket, seal in the enclave, SETTLE written to Base Sepolia, A pays B"
if [ "$PAID" = "1" ]; then
  run "python3 scripts/demo.py --buyer-max 120 --seller-min 90 --broadcast --settle-transfer 2>&1 | grep -v '^bo-value'"
else
  run "python3 scripts/demo.py --buyer-max 120 --seller-min 90 --broadcast --settle-transfer --no-pay"
fi
say "t=4 Read the receiver back from Base Sepolia — the clearing price and both commitments, nothing else"
run "cast call 0xA2eB7d6EEd6a4d0976cb29B226093E007E720590 'settlementCount()(uint256)' --rpc-url https://sepolia.base.org"
say "t=5 Now the other case: bands that do not overlap"
run "python3 scripts/demo.py --buyer-max 90 --seller-min 120 --broadcast --no-pay"
say "t=6 NO_OVERLAP carries nothing — not the gap, not which side was higher — and sends no transaction"
run "cast call 0xA2eB7d6EEd6a4d0976cb29B226093E007E720590 'settlementCount()(uint256)' --rpc-url https://sepolia.base.org"
say "t=7 Every payment and every settlement is verifiable on Basescan without trusting us. SETTLE reveals A_max + B_min — stated, not hidden."
sleep 3
say "t=8 github.com/craigmbrown/ethonline-sealed-bid — Chainlink CRE · Base · BlindOracle"
sleep 3
