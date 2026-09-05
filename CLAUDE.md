# CLAUDE.md — ethonline-sealed-bid

This is a standalone ETHOnline 2026 hackathon project. It has no relationship to any other
repository on this machine, and you must not read from, reference, or copy any directory
outside this one.

## Hard rules

1. **Work only inside this repository.** Do not open, search, or cite files outside
   `~/ethonline-sealed-bid/`. If a task seems to need something from elsewhere, stop and ask.
2. **No pre-existing project code.** Everything is written fresh from `SPEC.md` after
   2026-09-04 05:00 UTC. Do not "look for an existing implementation" anywhere.
3. **BlindOracle is a public HTTP API, nothing more.** Call it at
   `https://api.craigmbrown.com`. Never vendor a client library that is not on PyPI, and
   never reference how it is implemented server-side.
4. **Secrets never enter the repo.** `.env` is gitignored; `.env.sample` holds placeholders
   only. Before every commit, grep the diff for keys, tokens and private keys.
5. **Reserve prices are secret by design.** No log line, return value, test fixture output,
   or dispute payload may contain a party's reserve price outside the enclave. The no-leak
   tests in `sealed-bid-ts/main.test.ts` are the load-bearing tests; keep them passing.
6. **Commit small and often** — descriptive messages, never squash. The commit history is
   part of the submission. From Task 5 on, each task is a branch (`task-N-<slug>`) with
   at least two commits, merged to `main` through a pull request **with a merge commit**
   (never squash: that would collapse the history into the single dump the rules
   presume unqualified). Tasks 1–4 and 6 were committed directly to `main` on days 1–2.
7. **Real-money actions** (mainnet gas, paid BlindOracle calls above $1, the ETHGlobal
   stake) are done by the human operator, not by an agent. Ask first.

## Stack

- Chainlink CRE CLI v1.31.0 (`cre`), TypeScript workflows run with `bun`. Do NOT run
  `cre update`.
- Target chain: Base Sepolia (`ethereum-testnet-sepolia-base-1`) for settlement.
- Python 3.11 for `bo_client.py`, `scripts/demo.py`, `scripts/skucheck.py`.

## Commands

```bash
cre workflow simulate ./sealed-bid-ts --target=staging-settings --non-interactive --trigger-index 0
cd sealed-bid-ts && bun test
python3 scripts/skucheck.py        # run at the start of every session
```
