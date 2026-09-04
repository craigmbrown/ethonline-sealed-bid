// No-leak checkers (SPEC.md §2.1 output contract). Test-side helpers, kept in
// their own module so the negative-control tests can assert that the checkers
// themselves catch a leak — a checker that never fires proves nothing.

import { decodeAbiParameters, parseAbiParameters } from 'viem'
import { PRICE_SCALE } from './workflow'

/** Everything the enclave emitted on one run, exactly as captured. */
export type CapturedRun = {
	logs: string[]
	returned: string
	reportPayloadsB64: string[]
}

export const REPORT_ABI = parseAbiParameters('string result, uint256 clearingPriceMicro, string runLabel')

const b64ToHex = (b64: string): `0x${string}` => `0x${Buffer.from(b64, 'base64').toString('hex')}`

/** Decode every report payload into its typed fields. */
export const decodeReports = (run: CapturedRun) =>
	run.reportPayloadsB64.map((b64) => {
		const [result, clearingPriceMicro, runLabel] = decodeAbiParameters(REPORT_ABI, b64ToHex(b64))
		return { result, clearingPriceMicro, runLabel }
	})

/**
 * Throws if any reserve value appears anywhere in the captured outputs:
 *  - as a substring of a log line or the return string,
 *  - as a substring of any decoded string field of a report,
 *  - as the exact fixed-point value of any uint field of a report.
 */
export const assertNoReserveLeak = (run: CapturedRun, reserves: string[]): void => {
	const reserveMicros = reserves.map((r) => BigInt(Math.round(Number(r) * PRICE_SCALE)))
	const textOutputs = [...run.logs, run.returned]
	for (const line of textOutputs) {
		for (const r of reserves) {
			if (line.includes(r)) throw new Error(`reserve leaked into text output: "${line}"`)
		}
	}
	for (const rep of decodeReports(run)) {
		for (const r of reserves) {
			if (rep.result.includes(r) || rep.runLabel.includes(r)) {
				throw new Error(`reserve leaked into a report string field`)
			}
		}
		if (reserveMicros.includes(rep.clearingPriceMicro)) {
			throw new Error(`reserve leaked into report uint field (clearingPriceMicro == a reserve)`)
		}
	}
}

/**
 * Throws unless two captured runs are byte-identical in every emitted channel.
 * Used to prove NO_OVERLAP reveals no ordering or distance information: two
 * non-overlapping pairs with very different gaps must produce identical output.
 */
export const assertIdenticalRuns = (x: CapturedRun, y: CapturedRun): void => {
	const sx = JSON.stringify(x)
	const sy = JSON.stringify(y)
	if (sx !== sy) throw new Error(`enclave output differs between runs:\n${sx}\n${sy}`)
}
