import { describe, expect } from 'bun:test'
import type { TeeRuntime } from '@chainlink/cre-sdk'
import { test } from '@chainlink/cre-sdk/test'
import { type Config, initWorkflow, onCronTrigger, parseReserve, validateInputs } from './workflow'

// Task 2 skeleton tests. The load-bearing no-leak tests are Task 3 and will
// extend `makeFakeTeeRuntime` with a captured-output assertion.

const makeConfig = (): Config => ({
	schedule: '0 */1 * * * *',
	reserveSecretIdA: 'RESERVE_PRICE_AGENT_A',
	reserveSecretIdB: 'RESERVE_PRICE_AGENT_B',
	runLabel: 'test',
})

type Secrets = Record<string, string | undefined>

// The public test surface does not ship a TEE runtime factory, so stand up the
// slice of `TeeRuntime` the handler uses: config, getSecrets, log, usingTheDons.
const makeFakeTeeRuntime = (secrets: Secrets) => {
	const logs: string[] = []
	const reports: unknown[] = []
	const secretCalls: number[] = []

	const runtime = {
		config: makeConfig(),
		getSecrets: (requests: Array<{ id?: string }>) => {
			secretCalls.push(requests.length)
			return {
				result: () =>
					Object.fromEntries(
						requests.map((r) => [r.id, { id: r.id, value: secrets[r.id ?? ''] }]),
					),
			}
		},
		log: (message: string) => logs.push(message),
		usingTheDons: () => ({
			report: (input: unknown) => {
				reports.push(input)
				return { result: () => ({}) }
			},
		}),
	}

	return { runtime: runtime as unknown as TeeRuntime<Config>, logs, reports, secretCalls }
}

describe('parseReserve', () => {
	test('accepts positive integers and decimals', () => {
		expect(parseReserve('100')).toBe(100)
		expect(parseReserve(' 12.5 ')).toBe(12.5)
	})
	test('rejects missing, empty, zero, negative and non-numeric', () => {
		expect(parseReserve(undefined)).toBeNull()
		expect(parseReserve('')).toBeNull()
		expect(parseReserve('0')).toBeNull()
		expect(parseReserve('-5')).toBeNull()
		expect(parseReserve('1e3')).toBeNull()
		expect(parseReserve('abc')).toBeNull()
	})
})

describe('validateInputs', () => {
	test('both valid → SEALED_INPUTS_OK, either invalid → INVALID_INPUT', () => {
		expect(validateInputs(1, 2)).toBe('SEALED_INPUTS_OK')
		expect(validateInputs(null, 2)).toBe('INVALID_INPUT')
		expect(validateInputs(1, null)).toBe('INVALID_INPUT')
	})
})

describe('onCronTrigger', () => {
	test('reads both reserve prices in ONE getSecrets call', () => {
		const { runtime, secretCalls } = makeFakeTeeRuntime({
			RESERVE_PRICE_AGENT_A: '120',
			RESERVE_PRICE_AGENT_B: '90',
		})
		onCronTrigger(runtime)
		expect(secretCalls).toEqual([2])
	})

	test('returns SEALED_INPUTS_OK for two valid reserves', () => {
		const { runtime } = makeFakeTeeRuntime({ RESERVE_PRICE_AGENT_A: '120', RESERVE_PRICE_AGENT_B: '90' })
		expect(onCronTrigger(runtime)).toContain('SEALED_INPUTS_OK')
	})

	test('returns INVALID_INPUT when a reserve is missing or malformed', () => {
		const { runtime } = makeFakeTeeRuntime({ RESERVE_PRICE_AGENT_A: '120' })
		expect(onCronTrigger(runtime)).toContain('INVALID_INPUT')
	})

	test('crosses back to the DON with an evm report', () => {
		const { runtime, reports } = makeFakeTeeRuntime({ RESERVE_PRICE_AGENT_A: '120', RESERVE_PRICE_AGENT_B: '90' })
		onCronTrigger(runtime)
		expect(reports).toHaveLength(1)
		expect(reports[0]).toMatchObject({ encoderName: 'evm', signingAlgo: 'ecdsa', hashingAlgo: 'keccak256' })
	})

	test('never logs or returns a reserve price (skeleton form of the Task 3 no-leak test)', () => {
		const a = '731.25'
		const b = '412.5'
		const { runtime, logs } = makeFakeTeeRuntime({ RESERVE_PRICE_AGENT_A: a, RESERVE_PRICE_AGENT_B: b })
		const out = onCronTrigger(runtime)
		for (const line of [...logs, out]) {
			expect(line).not.toContain(a)
			expect(line).not.toContain(b)
		}
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
