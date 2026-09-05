// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import {IReceiver} from "./interfaces/IReceiver.sol";
import {IERC165} from "./interfaces/IERC165.sol";

/// @title SealedBidReceiver — Base Sepolia settlement record for the sealed-bid workflow.
///
/// @notice Receives the enclave's attestation via the Chainlink CRE Forwarder and records a
///         settlement ONLY when the enclave's result is `SETTLE`. Anything else reverts, so a
///         `NO_OVERLAP` or `INVALID_INPUT` run can never leave a trace on chain — even if a
///         misconfigured workflow tried to write one (SPEC.md §2.3: "On NO_OVERLAP, nothing is
///         written on chain"). The workflow itself also skips the write on non-SETTLE outcomes;
///         this contract is the second, independent layer.
///
/// @dev Report layout, exactly as the workflow ABI-encodes it (sealed-bid-ts/workflow.ts REPORT_ABI):
///        (string result, uint256 clearingPriceMicro, string runLabel,
///         bytes32 commitmentA, bytes32 commitmentB, bytes32 runId)
///      Only the clearing price and the two salted commitments are stored. No reserve price is
///      ever part of the report, so none can be recorded here.
///
///      Security model, in order of checks:
///        1. msg.sender must be the Forwarder (immutable, set at deployment) — the Forwarder is
///           the only party that has verified the DON's signatures over the report.
///        2. If an expected workflow owner is configured, the metadata's workflowOwner must match
///           — so a different owner's workflow delivered by the same Forwarder is rejected.
///        3. result must be "SETTLE"; the run id must be unused; the clearing price must be > 0.
contract SealedBidReceiver is IReceiver {
    struct Settlement {
        uint256 clearingPriceMicro; // clearing price, 6 decimals (USDC-style micro units)
        bytes32 commitmentA; // sha256(buyerMax || saltA), computed in the enclave
        bytes32 commitmentB; // sha256(sellerMin || saltB), computed in the enclave
        uint64 recordedAt; // block timestamp of the settlement tx
    }

    /// @notice The Chainlink CRE Forwarder on this chain. Only it may deliver reports.
    address public immutable FORWARDER;
    /// @notice If non-zero, only reports whose workflow owner equals this address are accepted.
    address public immutable EXPECTED_WORKFLOW_OWNER;

    mapping(bytes32 runId => Settlement) private s_settlements;
    uint256 public settlementCount;

    event Settled(
        bytes32 indexed runId,
        uint256 clearingPriceMicro,
        bytes32 commitmentA,
        bytes32 commitmentB,
        string runLabel
    );

    error InvalidForwarder();
    error UnauthorizedSender(address sender);
    error UnexpectedWorkflowOwner(address owner);
    error NotASettlement(string result);
    error ZeroClearingPrice();
    error RunAlreadySettled(bytes32 runId);
    error MalformedMetadata();

    constructor(address forwarder, address expectedWorkflowOwner) {
        if (forwarder == address(0)) revert InvalidForwarder();
        FORWARDER = forwarder;
        EXPECTED_WORKFLOW_OWNER = expectedWorkflowOwner;
    }

    /// @inheritdoc IReceiver
    function onReport(bytes calldata metadata, bytes calldata report) external override {
        if (msg.sender != FORWARDER) revert UnauthorizedSender(msg.sender);

        if (EXPECTED_WORKFLOW_OWNER != address(0)) {
            address owner = _workflowOwner(metadata);
            if (owner != EXPECTED_WORKFLOW_OWNER) revert UnexpectedWorkflowOwner(owner);
        }

        (
            string memory result,
            uint256 clearingPriceMicro,
            string memory runLabel,
            bytes32 commitmentA,
            bytes32 commitmentB,
            bytes32 runId
        ) = abi.decode(report, (string, uint256, string, bytes32, bytes32, bytes32));

        // The only outcome that may touch chain state.
        if (keccak256(bytes(result)) != keccak256("SETTLE")) revert NotASettlement(result);
        if (clearingPriceMicro == 0) revert ZeroClearingPrice();
        if (s_settlements[runId].recordedAt != 0) revert RunAlreadySettled(runId);

        s_settlements[runId] = Settlement({
            clearingPriceMicro: clearingPriceMicro,
            commitmentA: commitmentA,
            commitmentB: commitmentB,
            recordedAt: uint64(block.timestamp)
        });
        settlementCount += 1;

        emit Settled(runId, clearingPriceMicro, commitmentA, commitmentB, runLabel);
    }

    /// @notice The recorded settlement for a run id (recordedAt == 0 means none).
    function getSettlement(bytes32 runId) external view returns (Settlement memory) {
        return s_settlements[runId];
    }

    /// @inheritdoc IERC165
    function supportsInterface(bytes4 interfaceId) external pure override returns (bool) {
        return interfaceId == type(IReceiver).interfaceId || interfaceId == type(IERC165).interfaceId;
    }

    /// @dev metadata = abi.encodePacked(workflowId bytes32, workflowName bytes10, workflowOwner address) → 62 bytes.
    function _workflowOwner(bytes calldata metadata) private pure returns (address) {
        if (metadata.length < 62) revert MalformedMetadata();
        return address(bytes20(metadata[42:62]));
    }
}
