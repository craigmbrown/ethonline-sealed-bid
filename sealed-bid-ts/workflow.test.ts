import { describe, expect } from 'bun:test'
import type { TeeRuntime } from '@chainlink/cre-sdk'
import { test } from '@chainlink/cre-sdk/test'
import { assertIdenticalRuns, assertNoReserveLeak, decodeReports, type CapturedRun } from './noleak'
import {
	type Config,
	type EnclaveOutcome,
	type SealFn,
	initWorkflow,
	onCronTrigger,
	parseReserve,
	renderOutcome,
	runSealedBid,
	sealBids,
} from './workflow'

const makeConfig = (): Config => ({
	schedule: '0 */1 * * * *',
	reserveSecretIdA: 'RESERVE_PRICE_AGENT_A',
	reserveSecretIdB: 'RESERVE_PRICE_AGENT_B',
	runLabel: 'test',
})

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
const capture = (a: string | undefined, b: string | undefined, seal: SealFn = sealBids): CapturedRun => {
	const fake = makeFakeTeeRuntime({ RESERVE_PRICE_AGENT_A: a, RESERVE_PRICE_AGENT_B: b })
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

describe('sealBids (Task 3 placeholder — Task 4 adds the overlap maths)', () => {
	test('either input invalid → INVALID_INPUT with no clearing price', () => {
		expect(sealBids(null, 90)).toEqual({ result: 'INVALID_INPUT' })
		expect(sealBids(120, null)).toEqual({ result: 'INVALID_INPUT' })
	})
	test('NO_OVERLAP carries no clearing price', () => {
		expect(sealBids(120, 90)).toEqual({ result: 'NO_OVERLAP' })
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
	test('reads both reserve prices in ONE getSecrets call', () => {
		const fake = makeFakeTeeRuntime({ RESERVE_PRICE_AGENT_A: '120', RESERVE_PRICE_AGENT_B: '90' })
		onCronTrigger(fake.runtime)
		expect(fake.secretCalls).toEqual([2])
	})
	test('INVALID_INPUT when a reserve is missing or malformed', () => {
		expect(capture('120', undefined).returned).toContain('INVALID_INPUT')
		expect(capture('120', 'abc').returned).toContain('INVALID_INPUT')
	})
	test('crosses back to the DON with exactly one evm report carrying result + runLabel', () => {
		const run = capture('120', '90')
		const reports = decodeReports(run)
		expect(reports).toHaveLength(1)
		expect(reports[0].result).toBe('NO_OVERLAP')
		expect(reports[0].runLabel).toBe('test')
		expect(reports[0].clearingPriceMicro).toBe(0n)
	})
})

// ═══════════════════════════════════════════════════════════════════════════
// The load-bearing tests (SPEC.md Task 3). Everything above is plumbing.
// ═══════════════════════════════════════════════════════════════════════════

const RESERVE_PAIRS: Array<[string, string]> = [
	['120', '90'],
	['90', '120'],
	['731.25', '412.5'],
	['412.5', '731.25'],
	['1', '1000000'],
	['50', '50'],
	['0.000001', '999999.999999'],
]

describe('no-leak (a): a reserve price never appears in any non-enclave output', () => {
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
		for (const [a, b] of RESERVE_PAIRS) {
			expect(() => assertNoReserveLeak(capture(a, b, leakySealReturnsReserve), [a, b])).toThrow(/leaked/)
		}
	})
})

describe('no-leak (b): NO_OVERLAP reveals nothing about ordering or distance', () => {
	const near = capture('100', '101')
	const far = capture('1', '1000000')
	const reversed = capture('1000000', '1')

	test('production seal: near / far / reversed non-overlapping pairs are byte-identical', () => {
		expect(() => assertIdenticalRuns(near, far)).not.toThrow()
		expect(() => assertIdenticalRuns(near, reversed)).not.toThrow()
		expect(decodeReports(near)[0].result).toBe('NO_OVERLAP')
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
