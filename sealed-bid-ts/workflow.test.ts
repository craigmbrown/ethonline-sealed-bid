import { describe, expect } from 'bun:test'
import { type Report, type TeeRuntime, TxStatus } from '@chainlink/cre-sdk'
import { test } from '@chainlink/cre-sdk/test'
import { assertIdenticalRuns, assertNoReserveLeak, decodeReports, numericTokens, type CapturedRun } from './noleak'
import {
	type Config,
	type EnclaveOutcome,
	type SealFn,
	type WriteFn,
	ZERO32,
	ZERO_ADDRESS,
	commit,
	configSchema,
	initWorkflow,
	onCronTrigger,
	parseReserve,
	renderOutcome,
	runIdFor,
	runSealedBid,
	sealBids,
	settlementEnabled,
	toMicro,
} from './workflow'

/** The SealedBidReceiver deployed on Base Sepolia (contracts/src/SealedBidReceiver.sol). */
const RECEIVER = '0xaDF984468f5C7DEeb82FA4c98f25CA3952921ce7'

const makeConfig = (overrides: Partial<Config> = {}): Config => ({
	schedule: '0 */1 * * * *',
	reserveSecretIdA: 'RESERVE_PRICE_AGENT_A',
	reserveSecretIdB: 'RESERVE_PRICE_AGENT_B',
	saltSecretIdA: 'COMMITMENT_SALT_A',
	saltSecretIdB: 'COMMITMENT_SALT_B',
	runLabel: 'test',
	chainSelectorName: 'ethereum-testnet-sepolia-base-1',
	receiverAddress: RECEIVER,
	writeGasLimit: '300000',
	...overrides,
})

const SALT_A = 'salt-a-0f3c9e'
const SALT_B = 'salt-b-77d1a2'
const FAKE_TX = `0x${'ab'.repeat(32)}` as const

type Secrets = Record<string, string | undefined>

/** What the fake DON runtime hands to the writer: the same payload it reported. */
type FakeReport = { payloadB64: string }
/** One captured on-chain write attempt. */
type FakeWrite = { receiver: string; payloadB64: string; gasLimit: string }

// The public test surface does not ship a TEE runtime factory, so stand up the
// slice of `TeeRuntime` the handler uses: config, getSecrets, log, usingTheDons
// (report + log). Every channel the enclave can emit on is captured, and the
// DON-side chain write is captured through an injected `WriteFn`.
const makeFakeTeeRuntime = (secrets: Secrets, configOverrides: Partial<Config> = {}) => {
	const logs: string[] = []
	const reportPayloadsB64: string[] = []
	const secretCalls: number[] = []

	const runtime = {
		config: makeConfig(configOverrides),
		getSecrets: (requests: Array<{ id?: string }>) => {
			secretCalls.push(requests.length)
			return {
				result: () =>
					Object.fromEntries(requests.map((r) => [r.id, { id: r.id, value: secrets[r.id ?? ''] }])),
			}
		},
		log: (message: string) => logs.push(message),
		usingTheDons: () => ({
			log: (message: string) => logs.push(message),
			report: (input: { encodedPayload: string }) => {
				reportPayloadsB64.push(input.encodedPayload)
				return { result: (): FakeReport => ({ payloadB64: input.encodedPayload }) }
			},
		}),
	}

	return { runtime: runtime as unknown as TeeRuntime<Config>, logs, reportPayloadsB64, secretCalls }
}

/** A chain writer that records every attempt and answers with a fixed status. */
const recordingWriter = (writes: FakeWrite[], txStatus: TxStatus = TxStatus.SUCCESS): WriteFn => {
	return (_don, report, config) => {
		writes.push({
			receiver: config.receiverAddress,
			payloadB64: (report as unknown as FakeReport).payloadB64,
			gasLimit: config.writeGasLimit,
		})
		return { txStatus, txHash: FAKE_TX }
	}
}

type CaptureOptions = {
	seal?: SealFn
	salts?: { a?: string; b?: string }
	config?: Partial<Config>
	writeStatus?: TxStatus
}

/** Run the handler and capture every emitted channel, including chain writes. */
const capture = (
	a: string | undefined,
	b: string | undefined,
	seal: SealFn = sealBids,
	salts: { a?: string; b?: string } = { a: SALT_A, b: SALT_B },
	options: Omit<CaptureOptions, 'seal' | 'salts'> = {},
): CapturedRun => {
	const fake = makeFakeTeeRuntime(
		{
			RESERVE_PRICE_AGENT_A: a,
			RESERVE_PRICE_AGENT_B: b,
			COMMITMENT_SALT_A: salts.a,
			COMMITMENT_SALT_B: salts.b,
		},
		options.config ?? {},
	)
	const writes: FakeWrite[] = []
	const returned = runSealedBid(fake.runtime, seal, recordingWriter(writes, options.writeStatus))
	return { logs: fake.logs, returned, reportPayloadsB64: fake.reportPayloadsB64, writes }
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
		// The production entrypoint with the production writer; the zero receiver
		// keeps it off-chain (there is no chain in a unit test).
		const fake = makeFakeTeeRuntime(
			{
				RESERVE_PRICE_AGENT_A: '120',
				RESERVE_PRICE_AGENT_B: '90',
				COMMITMENT_SALT_A: SALT_A,
				COMMITMENT_SALT_B: SALT_B,
			},
			{ receiverAddress: ZERO_ADDRESS },
		)
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
		expect(capture('120', '90').returned).toBe(`SETTLE @ 105 (run: test) tx: ${FAKE_TX}`)
		expect(capture('90', '120').returned).toBe('NO_OVERLAP (run: test)')
	})
})

// ═══════════════════════════════════════════════════════════════════════════
// Settlement write (SPEC.md §2.3 / Task 6): the DON writes to the receiver on
// SETTLE and on nothing else. The receiver contract enforces the same rule
// independently (contracts/test); this is the workflow-side half.
// ═══════════════════════════════════════════════════════════════════════════

describe('settlement write — SPEC §2.3', () => {
	test('SETTLE writes exactly once, to the configured receiver, with the reported payload', () => {
		const run = capture('120', '90')
		expect(run.writes).toHaveLength(1)
		expect(run.writes?.[0].receiver).toBe(RECEIVER)
		expect(run.writes?.[0].gasLimit).toBe('300000')
		// The bytes handed to the chain are the very bytes the enclave reported.
		expect(run.writes?.[0].payloadB64).toBe(run.reportPayloadsB64[0])
		expect(decodeReports(run)[0].result).toBe('SETTLE')
		expect(run.logs.some((l) => l.includes(`settlement: written to ${RECEIVER} tx=${FAKE_TX}`))).toBe(true)
	})
	test('NO_OVERLAP never writes', () => {
		for (const [a, b] of [
			['90', '120'],
			['1', '1000000'],
			['100', '101'],
		]) {
			const run = capture(a, b)
			expect(decodeReports(run)[0].result).toBe('NO_OVERLAP')
			expect(run.writes).toEqual([])
			expect(run.returned).not.toContain('tx:')
		}
	})
	test('INVALID_INPUT never writes', () => {
		for (const run of [capture('120', 'abc'), capture(undefined, '90'), capture('120', '90', sealBids, { a: SALT_A })]) {
			expect(decodeReports(run)[0].result).toBe('INVALID_INPUT')
			expect(run.writes).toEqual([])
		}
	})
	test('the zero receiver address disables the write even on SETTLE (pure-simulation mode)', () => {
		const run = capture('120', '90', sealBids, { a: SALT_A, b: SALT_B }, { config: { receiverAddress: ZERO_ADDRESS } })
		expect(decodeReports(run)[0].result).toBe('SETTLE')
		expect(run.writes).toEqual([])
		expect(run.returned).toBe('SETTLE @ 105 (run: test)')
		expect(settlementEnabled(makeConfig({ receiverAddress: ZERO_ADDRESS }))).toBe(false)
		expect(settlementEnabled(makeConfig())).toBe(true)
	})
	test('a reverted or fatal write fails the run without naming a reserve', () => {
		for (const status of [TxStatus.REVERTED, TxStatus.FATAL]) {
			let message = ''
			try {
				capture('120', '90', sealBids, { a: SALT_A, b: SALT_B }, { writeStatus: status })
			} catch (e) {
				message = (e as Error).message
			}
			expect(message).toContain('settlement write failed')
			expect(message).toContain(TxStatus[status])
			for (const tok of numericTokens(message)) expect([120, 90]).not.toContain(tok)
		}
	})
	test('the write is a function of the report only — no reserve reaches the chain', () => {
		for (const [a, b] of RESERVE_PAIRS) {
			const run = capture(a, b)
			for (const w of run.writes ?? []) {
				const [rep] = decodeReports({ ...run, reportPayloadsB64: [w.payloadB64] })
				expect(() => assertNoReserveLeak({ ...run, reportPayloadsB64: [w.payloadB64] }, [a, b])).not.toThrow()
				expect(rep.result).toBe('SETTLE')
			}
		}
	})
	test('config schema defaults: chain, zero receiver, gas limit', () => {
		const parsed = configSchema.parse({
			schedule: '0 */1 * * * *',
			reserveSecretIdA: 'A',
			reserveSecretIdB: 'B',
			saltSecretIdA: 'SA',
			saltSecretIdB: 'SB',
			runLabel: 'x',
		})
		expect(parsed.chainSelectorName).toBe('ethereum-testnet-sepolia-base-1')
		expect(parsed.receiverAddress).toBe(ZERO_ADDRESS)
		expect(parsed.writeGasLimit).toBe('300000')
		expect(() => configSchema.parse({ ...parsed, receiverAddress: 'not-an-address' })).toThrow()
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
