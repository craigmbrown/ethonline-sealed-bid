// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import {IERC165} from "./IERC165.sol";

/// @title IReceiver — the contract surface a Chainlink CRE Forwarder delivers workflow reports to.
/// @notice The Forwarder verifies the DON signatures, then calls `onReport(metadata, report)`.
///         `metadata` is abi.encodePacked(workflowId bytes32, workflowName bytes10, workflowOwner address).
interface IReceiver is IERC165 {
    function onReport(bytes calldata metadata, bytes calldata report) external;
}
