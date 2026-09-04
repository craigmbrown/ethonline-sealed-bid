import { cre, hexToBase64, type TeeRuntime } from '@chainlink/cre-sdk'
import { encodeAbiParameters, parseAbiParameters } from 'viem'
import { z } from 'zod'

// ─── Config ────────────────────────────────────────────────────────────────
// Nothing in the config is secret. The reserve prices are Vault secrets and are
// only ever materialised inside the enclave (see `runSealedBid`).
export const configSchema = z.object({
	schedule: z.string(),
	reserveSecretIdA: z.string(),
	reserveSecretIdB: z.string(),
	runLabel: z.string(),
})
export type Config = z.infer<typeof configSchema>

// ─── Enclave outcome ───────────────────────────────────────────────────────
// This is the ONLY thing allowed to leave the enclave (SPEC.md §2.1 output
// contract). `clearingPrice` is present iff result === 'SETTLE'. Nothing else —
// no reserve, no gap, no ordering hint — may be attached to this object.
export type Result = 'SETTLE' | 'NO_OVERLAP' | 'INVALID_INPUT'
export type EnclaveOutcome = { result: Result; clearingPrice?: number }

/** Fixed-point encoding of a price for the on-chain report (6 decimals, USDC-style). */
export const PRICE_SCALE = 1_000_000
export const toMicro = (price: number): bigint => BigInt(Math.round(price * PRICE_SCALE))

/**
 * Parse a reserve price that arrived from the Vault. Runs inside the enclave.
 * Returns null on anything that is not a finite, strictly positive decimal.
 * Never throws with the raw value in the message — a thrown error can leave
 * the enclave as a log line.
 */
export const parseReserve = (raw: string | undefined): number | null => {
	if (raw === undefined) return null
	const trimmed = raw.trim()
	if (trimmed === '' || !/^[0-9]+(\.[0-9]+)?$/.test(trimmed)) return null
	const n = Number(trimmed)
	return Number.isFinite(n) && n > 0 ? n : null
}

/**
 * The sealing function: (buyer max, seller min) → outcome. Pure, deterministic,
 * runs inside the enclave.
 *
 * Task 3 state: placeholder. Both inputs are validated; the band-overlap
 * computation of SPEC.md §2.1 is Task 4 and replaces the marked line. The
 * no-leak tests in workflow.test.ts constrain what any future version may emit.
 */
export type SealFn = (buyerMax: number | null, sellerMin: number | null) => EnclaveOutcome
export const sealBids: SealFn = (buyerMax, sellerMin) => {
	if (buyerMax === null || sellerMin === null) return { result: 'INVALID_INPUT' }
	// Task 4 replaces this line with: overlap iff sellerMin <= buyerMax; midpoint clears.
	return { result: 'NO_OVERLAP' }
}

/** Human-readable rendering of an outcome. The only string form the enclave returns. */
export const renderOutcome = (o: EnclaveOutcome): string =>
	o.result === 'SETTLE' ? `SETTLE @ ${o.clearingPrice}` : o.result

// ─── TEE handler ───────────────────────────────────────────────────────────
// Receives a `TeeRuntime`. Everything here runs inside the enclave until we
// explicitly cross back with `usingTheDons()`. The only things that cross are
// derived from `EnclaveOutcome` — never a reserve price.
//
// `seal` is injectable so the test suite can run the identical output path
// against deliberately leaky sealing functions and prove the no-leak checks
// catch them. Production always uses `sealBids`.
export const runSealedBid = (runtime: TeeRuntime<Config>, seal: SealFn = sealBids): string => {
	const config = runtime.config

	// ONE getSecrets call for the whole execution (measured runtime limit:
	// a second call fails; up to 9 ids per call).
	const secrets = runtime
		.getSecrets([{ id: config.reserveSecretIdA }, { id: config.reserveSecretIdB }])
		.result()

	const buyerMax = parseReserve(secrets[config.reserveSecretIdA]?.value)
	const sellerMin = parseReserve(secrets[config.reserveSecretIdB]?.value)

	const outcome = seal(buyerMax, sellerMin)

	// Log lines may leave the enclave in simulation. Outcome only.
	runtime.log(`enclave: outcome=${outcome.result}`)

	// Cross back to the DON with a report carrying only the outcome.
	const donRuntime = runtime.usingTheDons()
	const encodedPayload = encodeAbiParameters(
		parseAbiParameters('string result, uint256 clearingPriceMicro, string runLabel'),
		[outcome.result, outcome.clearingPrice !== undefined ? toMicro(outcome.clearingPrice) : 0n, config.runLabel],
	)
	donRuntime
		.report({
			encodedPayload: hexToBase64(encodedPayload),
			encoderName: 'evm',
			signingAlgo: 'ecdsa',
			hashingAlgo: 'keccak256',
		})
		.result()

	return `${renderOutcome(outcome)} (run: ${config.runLabel})`
}

export const onCronTrigger = (runtime: TeeRuntime<Config>): string => runSealedBid(runtime)

export function initWorkflow(config: Config) {
	const cronTrigger = new cre.capabilities.CronCapability()

	return [
		// `cre.handlerInTee` — not `cre.handler`. The third argument is the
		// TeeConstraint: AWS Nitro in us-west-2 is the only registered TEE today.
		cre.handlerInTee(cronTrigger.trigger({ schedule: config.schedule }), onCronTrigger, [
			{ tee: 'nitro', regions: ['us-west-2'] },
		]),
	]
}
