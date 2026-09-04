// No-leak checkers (SPEC.md §2.1 output contract). Test-side helpers, kept in
// their own module so the negative-control tests can assert that the checkers
// themselves catch a leak — a checker that never fires proves nothing.
//
// The rule the checkers enforce is "output ⊆ f(inputs)": everything the enclave
// emits must be a function of the protocol, never of a raw input.
//   - A reserve value may appear in the output ONLY as the SETTLE clearing
//     price, and only when that clearing price is the protocol midpoint
//     (equal bands necessarily settle at the shared value — the documented
//     trade-off in SPEC.md §2.1).
//   - On NO_OVERLAP / INVALID_INPUT, everything except the two salted
//     commitments must be byte-identical regardless of the inputs.

import { decodeAbiParameters } from 'viem'
import { PRICE_SCALE, REPORT_ABI, toMicro, type Attestation } from './workflow'

/** Everything the enclave emitted on one run, exactly as captured. */
export type CapturedRun = {
	logs: string[]
	returned: string
	reportPayloadsB64: string[]
}

const b64ToHex = (b64: string): `0x${string}` => `0x${Buffer.from(b64, 'base64').toString('hex')}`

/** Decode every report payload into its typed fields. */
export const decodeReports = (run: CapturedRun): Attestation[] =>
	run.reportPayloadsB64.map((b64) => {
		const [result, clearingPriceMicro, runLabel, commitmentA, commitmentB, runId] = decodeAbiParameters(
			REPORT_ABI,
			b64ToHex(b64),
		)
		return { result: result as Attestation['result'], clearingPriceMicro, runLabel, commitmentA, commitmentB, runId }
	})

/** Numeric tokens in a line, ignoring anything inside a 0x-hex word (hashes contain digits). */
export const numericTokens = (line: string): number[] =>
	(line.replace(/0x[0-9a-fA-F]+/g, ' ').match(/\d+(?:\.\d+)?/g) ?? []).map(Number)

/**
 * Throws if a reserve value appears in the captured outputs anywhere it is not
 * mathematically implied by the protocol:
 *  - as a numeric token of a log line or the return string (hex hashes excluded),
 *  - as a numeric token of a decoded report string field,
 *  - as the uint clearing price when the result is not SETTLE,
 *  - as a SETTLE clearing price that is NOT the midpoint of the two reserves.
 */
export const assertNoReserveLeak = (run: CapturedRun, reserves: [string, string]): void => {
	const [a, b] = reserves.map(Number) as [number, number]
	const midpointMicro = toMicro((a + b) / 2)
	const reports = decodeReports(run)

	const allowed = new Set<number>()
	for (const rep of reports) {
		if (rep.result === 'SETTLE') {
			if (rep.clearingPriceMicro !== midpointMicro) {
				throw new Error(`reserve leaked: SETTLE clearing price is not the protocol midpoint`)
			}
			allowed.add(Number(rep.clearingPriceMicro) / PRICE_SCALE)
		} else if (rep.clearingPriceMicro !== 0n) {
			throw new Error(`reserve leaked: non-SETTLE report carries a non-zero clearing price`)
		}
		for (const field of [rep.result, rep.runLabel]) {
			for (const tok of numericTokens(field)) {
				if (tok === a || tok === b) throw new Error(`reserve leaked into a report string field`)
			}
		}
	}

	for (const line of [...run.logs, run.returned]) {
		for (const tok of numericTokens(line)) {
			if ((tok === a || tok === b) && !allowed.has(tok)) {
				throw new Error(`reserve leaked into text output: "${line}"`)
			}
		}
	}
}

/** The commitment-free projection of a run: what must be identical across NO_OVERLAP inputs. */
export const projectNonCommitment = (run: CapturedRun) => ({
	logs: run.logs.map((l) => l.replace(/0x[0-9a-fA-F]{64}/g, '<hash>')),
	returned: run.returned,
	reports: decodeReports(run).map(({ result, clearingPriceMicro, runLabel }) => ({
		result,
		clearingPriceMicro: clearingPriceMicro.toString(),
		runLabel,
	})),
})

/**
 * Throws unless two captured runs are byte-identical in every emitted channel
 * once the salted commitments (which differ by construction) are projected out.
 * Used to prove NO_OVERLAP reveals no ordering or distance information.
 */
export const assertIdenticalRuns = (x: CapturedRun, y: CapturedRun): void => {
	const sx = JSON.stringify(projectNonCommitment(x))
	const sy = JSON.stringify(projectNonCommitment(y))
	if (sx !== sy) throw new Error(`enclave output differs between runs:\n${sx}\n${sy}`)
}
