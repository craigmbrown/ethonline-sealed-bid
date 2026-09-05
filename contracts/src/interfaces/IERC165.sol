// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

/// @dev Standard interface detection (EIP-165).
interface IERC165 {
    function supportsInterface(bytes4 interfaceId) external view returns (bool);
}
