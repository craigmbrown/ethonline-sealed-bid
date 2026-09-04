# Disclosure — Pre-existing Work and AI Assistance

Written in accordance with the ETHGlobal "Rules on Pre-existing Work" (ethglobal.com/rules),
which ask participants to disclose any pre-existing work in writing.

## 1. A private predecessor exists

The author operates a private agent marketplace (BlindOracle, `api.craigmbrown.com`) that
includes a closed-source two-party price-band negotiation routine and an escrow module.
**None of that code, its tests, its documentation, or its design notes were opened, copied,
adapted, or vendored into this repository.** Every line here was written fresh from a plain
English specification of the sealed-bid protocol, after the official start of hacking
(2026-09-04 05:00 UTC).

BlindOracle is consumed by this project **only as a public, paid, third-party HTTP API**
(the same endpoints any agent can call). No private SDK, gateway source, or internal
configuration is included. Where this project calls a BlindOracle service, the request and
the on-chain settlement receipt are recorded so the call can be independently verified.

## 2. AI coding agents were used during the event

Code, tests, and documentation in this repository were produced with the help of AI coding
agents (Claude Code) operated by the author, working from this repository's own
specification. The agents ran from this directory with the minimal `CLAUDE.md` at the root;
they were not given access to the private predecessor. ETHOnline's own official schedule
included a "Claude Code: AI Skills for Hackathon Builders" workshop; the rules page contains
no restriction on AI assistance.

## 3. No code predates kick-off

- Hacking began: **2026-09-04 05:00 UTC**
- First commit to this repository: after that time on 2026-09-04 (see `git log`)
- Reference material read before kick-off (no code written): Chainlink's public
  `cre-templates` repository (MIT) and its CRE Confidential Workflows documentation; the
  public BlindOracle service catalog at `https://api.craigmbrown.com/v1/services`.

## 4. Third-party code

- Chainlink CRE CLI, SDK and starter templates — MIT, used as the workflow framework.
- Any dependency pulled by `bun install` is listed in the lockfile with its own license.

The commit history is incremental and unsquashed by design; please read it as the record of
what was built when.
