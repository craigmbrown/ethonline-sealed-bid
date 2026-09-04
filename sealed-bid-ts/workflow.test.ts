import { describe, expect } from 'bun:test'
import type { TeeRuntime } from '@chainlink/cre-sdk'
import { test } from '@chainlink/cre-sdk/test'
import { assertIdenticalRuns, assertNoReserveLeak, decodeReports, numericTokens, type CapturedRun } from './noleak'
import {
	type Config,
	type EnclaveOutcome,
	type SealFn,
	ZERO32,
	commit,
	initWorkflow,
	onCronTrigger,
	parseReserve,
	renderOutcome,
	runIdFor,
	runSealedBid,
	sealBids,
	toMicro,
} from './workflow'

const makeConfig = (): Config => ({
	schedule: '0 */1 * * * *',
	reserveSecretIdA: 'RESERVE_PRICE_AGENT_A',
	reserveSecretIdB: 'RESERVE_PRICE_AGENT_B',
	saltSecretIdA: 'COMMITMENT_SALT_A',
	saltSecretIdB: 'COMMITMENT_SALT_B',
	runLabel: 'test',
})

const SALT_A = 'salt-a-0f3c9e'
const SALT_B = 'salt-b-77d1a2'

type Secrets = Record<string, string | undefined>

// The public test surface does not ship a TEE runtime factory, so stand up the
// slice of `TeeRuntime` the handler uses: config, getSecrets, log, usingTheDons.
// Every channel the enclave can emit on is captured.
const makeFakeTeeRuntime = (secrets: Secrets) => {
	const logs: string[] = []
	const reportPayloadsB64: string[] = []
	const secretCalls: number[] = []

	const runtime = {
		config: makeConfig(),
		getSecrets: (requests: Array<{ id?: string }>) => {
			secretCalls.push(requests.length)
			return {
				result: () =>
					Object.fromEntries(requests.map((r) => [r.id, { id: r.id, value: secrets[r.id ?? ''] }])),
			}
		},
		log: (message: string) => logs.push(message),
		usingTheDons: () => ({
			report: (input: { encodedPayload: string }) => {
				reportPayloadsB64.push(input.encodedPayload)
				return { result: () => ({}) }
			},
		}),
	}

	return { runtime: runtime as unknown as TeeRuntime<Config>, logs, reportPayloadsB64, secretCalls }
}

/** Run the handler and capture every emitted channel. */
const capture = (
	a: string | undefined,
	b: string | undefined,
	seal: SealFn = sealBids,
	salts: { a?: string; b?: string } = { a: SALT_A, b: SALT_B },
): CapturedRun => {
	const fake = makeFakeTeeRuntime({
		RESERVE_PRICE_AGENT_A: a,
		RESERVE_PRICE_AGENT_B: b,
		COMMITMENT_SALT_A: salts.a,
		COMMITMENT_SALT_B: salts.b,
	})
	const returned = runSealedBid(fake.runtime, seal)
	return { logs: fake.logs, returned, reportPayloadsB64: fake.reportPayloadsB64 }
}

// ─── Deliberately leaky sealing functions (negative controls) ──────────────
// These exist to prove the checkers fire. If either of them ever PASSES the
// no-leak tests, the checkers are broken and the real tests are meaningless.

/** Leaks a reserve by using it as the clearing price. */
const leakySealReturnsReserve: SealFn = (buyerMax, sellerMin) =>
	buyerMax === null || sellerMin === null ? { result: 'INVALID_INPUT' } : { result: 'SETTLE', clearingPrice: buyerMax }

/** Leaks the distance between bands through the uint report field on NO_OVERLAP. */
const leakySealRevealsGap: SealFn = (buyerMax, sellerMin) =>
	buyerMax === null || sellerMin === null
		? { result: 'INVALID_INPUT' }
		: ({ result: 'NO_OVERLAP', clearingPrice: Math.abs(buyerMax - sellerMin) } as EnclaveOutcome)

// ───────────────────────────────────────────────────────────────────────────

describe('parseReserve', () => {
	test('accepts positive integers and decimals', () => {
		expect(parseReserve('100')).toBe(100)
		expect(parseReserve(' 12.5 ')).toBe(12.5)
	})
	test('rejects missing, empty, zero, negative and non-numeric', () => {
		for (const bad of [undefined, '', '0', '-5', '1e3', 'abc']) expect(parseReserve(bad)).toBeNull()
	})
})

describe('sealBids — SPEC §2.1', () => {
	test('overlap: seller min below buyer max → SETTLE at the midpoint', () => {
		expect(sealBids(120, 90)).toEqual({ result: 'SETTLE', clearingPrice: 105 })
		expect(sealBids(731.25, 412.5)).toEqual({ result: 'SETTLE', clearingPrice: 571.875 })
	})
	test('no overlap: seller min above buyer max → NO_OVERLAP with no clearing price', () => {
		expect(sealBids(90, 120)).toEqual({ result: 'NO_OVERLAP' })
		expect(sealBids(1, 1_000_000)).toEqual({ result: 'NO_OVERLAP' })
	})
	test('equal bands → SETTLE at that price', () => {
		expect(sealBids(50, 50)).toEqual({ result: 'SETTLE', clearingPrice: 50 })
	})
	test('single-point overlap (one micro-unit apart) → SETTLE at the midpoint', () => {
		expect(sealBids(100.000001, 100)).toEqual({ result: 'SETTLE', clearingPrice: 100.0000005 })
	})
	test('either input invalid → INVALID_INPUT with no clearing price', () => {
		expect(sealBids(null, 90)).toEqual({ result: 'INVALID_INPUT' })
		expect(sealBids(120, null)).toEqual({ result: 'INVALID_INPUT' })
	})
})

describe('commitments — SPEC §2.2', () => {
	test('deterministic for the same (reserve, salt)', () => {
		expect(commit(120, SALT_A)).toBe(commit(120, SALT_A))
	})
	test('hiding: a different salt or a different reserve changes the commitment', () => {
		expect(commit(120, SALT_A)).not.toBe(commit(120, SALT_B))
		expect(commit(120, SALT_A)).not.toBe(commit(121, SALT_A))
	})
	test('a commitment does not contain the reserve as a numeric token', () => {
		for (const r of [120, 731.25, 50]) expect(numericTokens(commit(r, SALT_A))).toEqual([])
	})
	test('runId is a function of both commitments and the label', () => {
		const ca = commit(120, SALT_A)
		const cb = commit(90, SALT_B)
		expect(runIdFor(ca, cb, 'x')).toBe(runIdFor(ca, cb, 'x'))
		expect(runIdFor(ca, cb, 'x')).not.toBe(runIdFor(cb, ca, 'x'))
		expect(runIdFor(ca, cb, 'x')).not.toBe(runIdFor(ca, cb, 'y'))
	})
})

describe('renderOutcome', () => {
	test('renders each result form', () => {
		expect(renderOutcome({ result: 'SETTLE', clearingPrice: 105 })).toBe('SETTLE @ 105')
		expect(renderOutcome({ result: 'NO_OVERLAP' })).toBe('NO_OVERLAP')
		expect(renderOutcome({ result: 'INVALID_INPUT' })).toBe('INVALID_INPUT')
	})
})

describe('handler plumbing', () => {
	test('reads both reserves and both salts in ONE getSecrets call', () => {
		const fake = makeFakeTeeRuntime({
			RESERVE_PRICE_AGENT_A: '120',
			RESERVE_PRICE_AGENT_B: '90',
			COMMITMENT_SALT_A: SALT_A,
			COMMITMENT_SALT_B: SALT_B,
		})
		onCronTrigger(fake.runtime)
		expect(fake.secretCalls).toEqual([4])
	})
	test('INVALID_INPUT when a reserve is missing or malformed, or a salt is missing', () => {
		expect(capture('120', undefined).returned).toContain('INVALID_INPUT')
		expect(capture('120', 'abc').returned).toContain('INVALID_INPUT')
		expect(capture('120', '90', sealBids, { a: SALT_A }).returned).toContain('INVALID_INPUT')
	})
	test('SETTLE report: midpoint in micro-units, both commitments, runId', () => {
		const [rep] = decodeReports(capture('120', '90'))
		expect(rep.result).toBe('SETTLE')
		expect(rep.clearingPriceMicro).toBe(toMicro(105))
		expect(rep.runLabel).toBe('test')
		expect(rep.commitmentA).toBe(commit(120, SALT_A))
		expect(rep.commitmentB).toBe(commit(90, SALT_B))
		expect(rep.runId).toBe(runIdFor(rep.commitmentA, rep.commitmentB, 'test'))
	})
	test('NO_OVERLAP report: zero clearing price, commitments still present', () => {
		const [rep] = decodeReports(capture('90', '120'))
		expect(rep.result).toBe('NO_OVERLAP')
		expect(rep.clearingPriceMicro).toBe(0n)
		expect(rep.commitmentA).toBe(commit(90, SALT_A))
		expect(rep.commitmentB).toBe(commit(120, SALT_B))
	})
	test('INVALID_INPUT report: zero clearing price and zero commitments (nothing partial leaves)', () => {
		const [rep] = decodeReports(capture('120', 'abc'))
		expect(rep.result).toBe('INVALID_INPUT')
		expect(rep.clearingPriceMicro).toBe(0n)
		expect(rep.commitmentA).toBe(ZERO32)
		expect(rep.commitmentB).toBe(ZERO32)
	})
	test('simulate-shaped return strings', () => {
		expect(capture('120', '90').returned).toBe('SETTLE @ 105 (run: test)')
		expect(capture('90', '120').returned).toBe('NO_OVERLAP (run: test)')
	})
})

// ═══════════════════════════════════════════════════════════════════════════
// The load-bearing tests (SPEC.md Task 3). Everything above is plumbing.
// ═══════════════════════════════════════════════════════════════════════════

const RESERVE_PAIRS: Array<[string, string]> = [
	['120', '90'], // SETTLE @ 105
	['90', '120'], // NO_OVERLAP
	['731.25', '412.5'], // SETTLE @ 571.875
	['412.5', '731.25'], // NO_OVERLAP
	['1', '1000000'], // NO_OVERLAP
	['1000000', '1'], // SETTLE @ 500000.5
	['50', '50'], // SETTLE @ 50 — equal bands: the clearing price IS the shared reserve
	['0.000001', '999999.999999'], // NO_OVERLAP
]

describe('no-leak (a): a reserve price never appears outside what the protocol implies', () => {
	test('production seal: no reserve in logs, return value, or decoded report fields', () => {
		for (const [a, b] of RESERVE_PAIRS) {
			expect(() => assertNoReserveLeak(capture(a, b), [a, b])).not.toThrow()
		}
	})
	test('production seal: the malformed-input path does not echo the malformed value', () => {
		const junk = 'MY-SECRET-BID-731'
		const run = capture(junk, '90')
		for (const line of [...run.logs, run.returned]) expect(line).not.toContain(junk)
	})
	test('NEGATIVE CONTROL: a seal that uses a reserve as the clearing price is caught', () => {
		// Equal bands are excluded: there the midpoint equals both reserves by
		// construction, so this particular leak is indistinguishable from the
		// correct answer — which is exactly the trade-off SPEC §2.1 documents.
		for (const [a, b] of RESERVE_PAIRS.filter(([x, y]) => x !== y)) {
			expect(() => assertNoReserveLeak(capture(a, b, leakySealReturnsReserve), [a, b])).toThrow(/leaked/)
		}
	})
	test('NEGATIVE CONTROL: a SETTLE at a non-midpoint price is caught even when it names neither reserve', () => {
		const offMidpoint: SealFn = () => ({ result: 'SETTLE', clearingPrice: 104 })
		expect(() => assertNoReserveLeak(capture('120', '90', offMidpoint), ['120', '90'])).toThrow(/midpoint/)
	})
})

describe('no-leak (b): NO_OVERLAP reveals nothing about ordering or distance', () => {
	const near = capture('100', '101')
	const far = capture('1', '1000000')
	const reversedFar = capture('0.5', '999999')

	test('production seal: near / far / reversed non-overlapping pairs are identical outside the commitments', () => {
		expect(() => assertIdenticalRuns(near, far)).not.toThrow()
		expect(() => assertIdenticalRuns(near, reversedFar)).not.toThrow()
		expect(decodeReports(near)[0].result).toBe('NO_OVERLAP')
	})
	test('production seal: the commitments themselves carry no numeric token of either reserve', () => {
		for (const run of [near, far, reversedFar]) {
			const [rep] = decodeReports(run)
			expect(numericTokens(rep.commitmentA)).toEqual([])
			expect(numericTokens(rep.commitmentB)).toEqual([])
		}
	})
	test('NEGATIVE CONTROL: a seal that leaks the gap through the report is caught', () => {
		const leakyNear = capture('100', '101', leakySealRevealsGap)
		const leakyFar = capture('1', '1000000', leakySealRevealsGap)
		expect(() => assertIdenticalRuns(leakyNear, leakyFar)).toThrow(/differs/)
	})
	test('NEGATIVE CONTROL: the checker itself distinguishes a one-byte difference', () => {
		const x: CapturedRun = { logs: ['a'], returned: 'r', reportPayloadsB64: [] }
		const y: CapturedRun = { logs: ['b'], returned: 'r', reportPayloadsB64: [] }
		expect(() => assertIdenticalRuns(x, y)).toThrow(/differs/)
	})
})

describe('initWorkflow', () => {
	test('registers the cron handler with handlerInTee and a Nitro constraint', () => {
		const handlers = initWorkflow(makeConfig())
		expect(handlers).toHaveLength(1)
		expect(handlers[0].fn).toBe(onCronTrigger)
		// handlerInTee attaches TEE requirements; cre.handler does not.
		expect(handlers[0].requirements).toBeDefined()
	})
})
