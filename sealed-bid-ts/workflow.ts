import { cre, hexToBase64, type TeeRuntime } from '@chainlink/cre-sdk'
import { encodeAbiParameters, parseAbiParameters } from 'viem'
import { z } from 'zod'

// ─── Config ────────────────────────────────────────────────────────────────
// Nothing in the config is secret. The reserve prices are Vault secrets and are
// only ever materialised inside the enclave (see `onCronTrigger`).
export const configSchema = z.object({
	schedule: z.string(),
	reserveSecretIdA: z.string(),
	reserveSecretIdB: z.string(),
	runLabel: z.string(),
})
export type Config = z.infer<typeof configSchema>

// ─── Outcomes ──────────────────────────────────────────────────────────────
// The full protocol (SPEC.md §2.1) returns SETTLE / NO_OVERLAP / INVALID_INPUT.
// Task 2 (this commit) is the skeleton: it proves both reserve prices can be
// read inside the TEE handler in ONE getSecrets call and validated there, and
// that a non-sensitive status can cross back to the DON. The overlap maths is
// Task 4 and is deliberately not here yet — the no-leak tests (Task 3) land first.
export type Outcome = 'SEALED_INPUTS_OK' | 'INVALID_INPUT'

/**
 * Parse a reserve price that arrived from the Vault. Runs inside the enclave.
 * Returns null on anything that is not a finite, strictly positive number.
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
 * Skeleton validation step. Task 4 replaces this with the band-overlap
 * computation (SPEC.md §2.1) and the attestation record (§2.2).
 */
export const validateInputs = (a: number | null, b: number | null): Outcome =>
	a !== null && b !== null ? 'SEALED_INPUTS_OK' : 'INVALID_INPUT'

// ─── TEE handler ───────────────────────────────────────────────────────────
// Receives a `TeeRuntime`. Everything here runs inside the enclave until we
// explicitly cross back with `usingTheDons()`. The only things that cross are
// the outcome string and the run label — never a reserve price.
export const onCronTrigger = (runtime: TeeRuntime<Config>): string => {
	const config = runtime.config

	// ONE getSecrets call for the whole execution (measured runtime limit:
	// a second call fails; up to 9 ids per call).
	const secrets = runtime
		.getSecrets([{ id: config.reserveSecretIdA }, { id: config.reserveSecretIdB }])
		.result()

	const reserveA = parseReserve(secrets[config.reserveSecretIdA]?.value)
	const reserveB = parseReserve(secrets[config.reserveSecretIdB]?.value)

	const outcome = validateInputs(reserveA, reserveB)

	// Log lines may leave the enclave in simulation. Status only.
	runtime.log(`enclave: inputs=${outcome === 'SEALED_INPUTS_OK' ? 'both-valid' : 'invalid'} outcome=${outcome}`)

	// Cross back to the DON with a report carrying only the outcome.
	const donRuntime = runtime.usingTheDons()
	const encodedPayload = encodeAbiParameters(parseAbiParameters('string outcome, string runLabel'), [
		outcome,
		config.runLabel,
	])
	donRuntime
		.report({
			encodedPayload: hexToBase64(encodedPayload),
			encoderName: 'evm',
			signingAlgo: 'ecdsa',
			hashingAlgo: 'keccak256',
		})
		.result()

	return `${outcome} (run: ${config.runLabel})`
}

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
