import {
	EVMClient,
	TxStatus,
	bytesToHex,
	cre,
	getNetwork,
	hexToBase64,
	type Report,
	type Runtime,
	type TeeRuntime,
} from '@chainlink/cre-sdk'
import { concat, encodeAbiParameters, parseAbiParameters, sha256, stringToBytes, type Hex } from 'viem'
import { z } from 'zod'

// ─── Config ────────────────────────────────────────────────────────────────
// Nothing in the config is secret. The reserve prices and the commitment salts
// are Vault secrets and are only ever materialised inside the enclave.
export const ZERO_ADDRESS = '0x0000000000000000000000000000000000000000'
export const configSchema = z.object({
	schedule: z.string(),
	reserveSecretIdA: z.string(),
	reserveSecretIdB: z.string(),
	saltSecretIdA: z.string(),
	saltSecretIdB: z.string(),
	runLabel: z.string(),
	// Settlement chain (SPEC.md §2.3). `receiverAddress` = the deployed
	// SealedBidReceiver; the zero address disables the on-chain write entirely
	// (pure-simulation mode). The write happens ONLY on SETTLE.
	chainSelectorName: z.string().default('ethereum-testnet-sepolia-base-1'),
	receiverAddress: z
		.string()
		.regex(/^0x[0-9a-fA-F]{40}$/, 'receiverAddress must be a 20-byte hex address')
		.default(ZERO_ADDRESS),
	writeGasLimit: z.string().regex(/^[0-9]+$/).default('300000'),
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
 * The sealing function (SPEC.md §2.1): (buyer max, seller min) → outcome.
 * Pure, deterministic, runs inside the enclave.
 *
 *   overlap  iff  sellerMin <= buyerMax
 *   clearing  =   midpoint of the overlap  =  (buyerMax + sellerMin) / 2
 *
 * Equal bands settle at that price; a single-point overlap is the same case.
 * NO_OVERLAP carries nothing — not the gap, not which side was higher.
 */
export type SealFn = (buyerMax: number | null, sellerMin: number | null) => EnclaveOutcome
export const sealBids: SealFn = (buyerMax, sellerMin) => {
	if (buyerMax === null || sellerMin === null) return { result: 'INVALID_INPUT' }
	if (sellerMin <= buyerMax) return { result: 'SETTLE', clearingPrice: (buyerMax + sellerMin) / 2 }
	return { result: 'NO_OVERLAP' }
}

/** Human-readable rendering of an outcome. The only string form the enclave returns. */
export const renderOutcome = (o: EnclaveOutcome): string =>
	o.result === 'SETTLE' ? `SETTLE @ ${o.clearingPrice}` : o.result

// ─── Input commitments (SPEC.md §2.2) ──────────────────────────────────────
// commit(reserve, salt) = sha256(utf8(`${reserve}|${salt}`)). Lets a party later
// prove what it bid (by revealing reserve + salt to an arbiter) without the
// enclave ever having published the reserve. Computed inside the enclave; the
// salts are Vault secrets, so the commitment is hiding as long as the salt is
// unguessable.
export const ZERO32: Hex = `0x${'00'.repeat(32)}`
export const commit = (reserve: number, salt: string): Hex => sha256(stringToBytes(`${reserve}|${salt}`))

/** Deterministic id for one sealed pair: sha256(commitA ‖ commitB ‖ utf8(runLabel)). */
export const runIdFor = (commitA: Hex, commitB: Hex, runLabel: string): Hex =>
	sha256(concat([commitA, commitB, `0x${Buffer.from(runLabel, 'utf8').toString('hex')}`]))

// ─── Attestation record ────────────────────────────────────────────────────
// What the DON signs (ecdsa over the encoded payload) and what the on-chain
// receiver decodes. `executed_at` from the spec is deliberately NOT included:
// a wall-clock value is non-deterministic across nodes and would break both
// consensus and the byte-identity property tested in workflow.test.ts; the
// settlement transaction's block timestamp serves that purpose instead.
export const REPORT_ABI = parseAbiParameters(
	'string result, uint256 clearingPriceMicro, string runLabel, bytes32 commitmentA, bytes32 commitmentB, bytes32 runId',
)
export type Attestation = {
	result: Result
	clearingPriceMicro: bigint
	runLabel: string
	commitmentA: Hex
	commitmentB: Hex
	runId: Hex
}

// ─── Settlement write (SPEC.md §2.3) ───────────────────────────────────────
// The DON hands the signed report to the CRE Forwarder on the settlement chain,
// which verifies the signatures and calls SealedBidReceiver.onReport. This is
// invoked ONLY when the enclave's result is SETTLE — a NO_OVERLAP or
// INVALID_INPUT run never reaches this function, so nothing is written on chain
// (and the receiver independently reverts anything that is not a SETTLE).
//
// `WriteFn` is injectable so the test suite can prove the workflow-side rule
// (never called on non-SETTLE, called exactly once on SETTLE) without a chain.
export type WriteReceipt = { txStatus: TxStatus; txHash: Hex; errorMessage?: string }
export type WriteFn = (don: Runtime<Config>, report: Report, config: Config) => WriteReceipt

export const writeSettlement: WriteFn = (don, report, config) => {
	const network = getNetwork({ chainFamily: 'evm', chainSelectorName: config.chainSelectorName })
	if (!network) throw new Error(`unknown chain selector name: ${config.chainSelectorName}`)
	const evm = new EVMClient(network.chainSelector.selector)
	const reply = evm
		.writeReport(don, {
			receiver: config.receiverAddress,
			report,
			gasConfig: { gasLimit: config.writeGasLimit },
		})
		.result()
	return {
		txStatus: reply.txStatus,
		txHash: reply.txHash ? (bytesToHex(reply.txHash) as Hex) : ZERO32,
		errorMessage: reply.errorMessage,
	}
}

export const settlementEnabled = (config: Config): boolean => config.receiverAddress.toLowerCase() !== ZERO_ADDRESS

// ─── TEE handler ───────────────────────────────────────────────────────────
// Receives a `TeeRuntime`. Everything here runs inside the enclave until we
// explicitly cross back with `usingTheDons()`. The only things that cross are
// derived from `EnclaveOutcome` plus the salted commitments — never a reserve.
//
// `seal` and `write` are injectable so the test suite can run the identical
// output path against deliberately leaky sealing functions and a fake chain
// writer. Production always uses `sealBids` and `writeSettlement`.
export const runSealedBid = (
	runtime: TeeRuntime<Config>,
	seal: SealFn = sealBids,
	write: WriteFn = writeSettlement,
): string => {
	const config = runtime.config

	// ONE getSecrets call for the whole execution (measured runtime limit:
	// a second call fails; up to 9 ids per call). Four ids here.
	const secrets = runtime
		.getSecrets([
			{ id: config.reserveSecretIdA },
			{ id: config.reserveSecretIdB },
			{ id: config.saltSecretIdA },
			{ id: config.saltSecretIdB },
		])
		.result()

	const buyerMax = parseReserve(secrets[config.reserveSecretIdA]?.value)
	const sellerMin = parseReserve(secrets[config.reserveSecretIdB]?.value)
	const saltA = secrets[config.saltSecretIdA]?.value
	const saltB = secrets[config.saltSecretIdB]?.value

	// A missing salt is a configuration failure; treat it like a missing input
	// so nothing partial leaves the enclave.
	const saltsOk = typeof saltA === 'string' && saltA !== '' && typeof saltB === 'string' && saltB !== ''
	const outcome = saltsOk ? seal(buyerMax, sellerMin) : ({ result: 'INVALID_INPUT' } as EnclaveOutcome)

	const commitmentA = outcome.result !== 'INVALID_INPUT' ? commit(buyerMax as number, saltA as string) : ZERO32
	const commitmentB = outcome.result !== 'INVALID_INPUT' ? commit(sellerMin as number, saltB as string) : ZERO32
	const attestation: Attestation = {
		result: outcome.result,
		clearingPriceMicro: outcome.clearingPrice !== undefined ? toMicro(outcome.clearingPrice) : 0n,
		runLabel: config.runLabel,
		commitmentA,
		commitmentB,
		runId: runIdFor(commitmentA, commitmentB, config.runLabel),
	}

	// Log lines may leave the enclave in simulation. Outcome + hashes only.
	runtime.log(`enclave: outcome=${attestation.result} runId=${attestation.runId}`)
	runtime.log(`enclave: commitmentA=${attestation.commitmentA} commitmentB=${attestation.commitmentB}`)

	// Cross back to the DON with a report carrying only the attestation.
	const donRuntime = runtime.usingTheDons()
	const encodedPayload = encodeAbiParameters(REPORT_ABI, [
		attestation.result,
		attestation.clearingPriceMicro,
		attestation.runLabel,
		attestation.commitmentA,
		attestation.commitmentB,
		attestation.runId,
	])
	const report = donRuntime
		.report({
			encodedPayload: hexToBase64(encodedPayload),
			encoderName: 'evm',
			signingAlgo: 'ecdsa',
			hashingAlgo: 'keccak256',
		})
		.result()

	// SPEC §2.3: on SETTLE the DON writes the attestation to the receiver on
	// Base Sepolia; on anything else nothing is written on chain.
	let txNote = ''
	if (attestation.result === 'SETTLE' && settlementEnabled(config)) {
		const receipt = write(donRuntime, report, config)
		if (receipt.txStatus !== TxStatus.SUCCESS) {
			// The message carries chain status only — never an input.
			throw new Error(`settlement write failed: status=${TxStatus[receipt.txStatus]} tx=${receipt.txHash}`)
		}
		donRuntime.log(`settlement: written to ${config.receiverAddress} tx=${receipt.txHash}`)
		txNote = ` tx: ${receipt.txHash}`
	}

	return `${renderOutcome(outcome)} (run: ${config.runLabel})${txNote}`
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
