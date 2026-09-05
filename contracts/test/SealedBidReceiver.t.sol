// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import {Test} from "forge-std/Test.sol";
import {SealedBidReceiver} from "../src/SealedBidReceiver.sol";
import {IReceiver} from "../src/interfaces/IReceiver.sol";
import {IERC165} from "../src/interfaces/IERC165.sol";

/// Tests mirror the workflow's report ABI exactly:
///   (string result, uint256 clearingPriceMicro, string runLabel,
///    bytes32 commitmentA, bytes32 commitmentB, bytes32 runId)
contract SealedBidReceiverTest is Test {
    address internal constant FORWARDER = address(0xF8344CFd5c43616a4366C34E3EEE75af79a74482);
    address internal constant OWNER = address(0x2D0B6cd9485e59a6eDc10B048227FAF0e81D174D);
    address internal constant STRANGER = address(0xBEEF);

    bytes32 internal constant WORKFLOW_ID = keccak256("workflow-id");
    bytes10 internal constant WORKFLOW_NAME = bytes10("sealedbid");

    SealedBidReceiver internal receiver;

    bytes32 internal commitA = sha256("120|salt-a");
    bytes32 internal commitB = sha256("90|salt-b");
    bytes32 internal runId = sha256(abi.encodePacked(sha256("120|salt-a"), sha256("90|salt-b"), "test"));

    event Settled(
        bytes32 indexed runId,
        uint256 clearingPriceMicro,
        bytes32 commitmentA,
        bytes32 commitmentB,
        string runLabel
    );

    function setUp() public {
        receiver = new SealedBidReceiver(FORWARDER, OWNER);
    }

    // ── helpers ────────────────────────────────────────────────────────────

    function metadataFor(address owner) internal pure returns (bytes memory) {
        return abi.encodePacked(WORKFLOW_ID, WORKFLOW_NAME, owner);
    }

    function report(string memory result, uint256 priceMicro) internal view returns (bytes memory) {
        return abi.encode(result, priceMicro, "test", commitA, commitB, runId);
    }

    function deliver(address sender, bytes memory metadata, bytes memory rep) internal {
        vm.prank(sender);
        receiver.onReport(metadata, rep);
    }

    // ── the SETTLE path ────────────────────────────────────────────────────

    function test_settleRecordsClearingPriceAndCommitments() public {
        vm.expectEmit(true, false, false, true, address(receiver));
        emit Settled(runId, 105_000_000, commitA, commitB, "test");

        deliver(FORWARDER, metadataFor(OWNER), report("SETTLE", 105_000_000));

        SealedBidReceiver.Settlement memory s = receiver.getSettlement(runId);
        assertEq(s.clearingPriceMicro, 105_000_000);
        assertEq(s.commitmentA, commitA);
        assertEq(s.commitmentB, commitB);
        assertEq(s.recordedAt, uint64(block.timestamp));
        assertEq(receiver.settlementCount(), 1);
    }

    function test_settleTwiceForSameRunReverts() public {
        deliver(FORWARDER, metadataFor(OWNER), report("SETTLE", 105_000_000));
        vm.expectRevert(abi.encodeWithSelector(SealedBidReceiver.RunAlreadySettled.selector, runId));
        deliver(FORWARDER, metadataFor(OWNER), report("SETTLE", 105_000_000));
    }

    function test_settleWithZeroPriceReverts() public {
        vm.expectRevert(SealedBidReceiver.ZeroClearingPrice.selector);
        deliver(FORWARDER, metadataFor(OWNER), report("SETTLE", 0));
    }

    // ── nothing else may touch chain state (SPEC §2.3) ─────────────────────

    function test_noOverlapReportRevertsAndWritesNothing() public {
        vm.expectRevert(abi.encodeWithSelector(SealedBidReceiver.NotASettlement.selector, "NO_OVERLAP"));
        deliver(FORWARDER, metadataFor(OWNER), report("NO_OVERLAP", 0));
        assertEq(receiver.settlementCount(), 0);
        assertEq(receiver.getSettlement(runId).recordedAt, 0);
    }

    function test_invalidInputReportReverts() public {
        vm.expectRevert(abi.encodeWithSelector(SealedBidReceiver.NotASettlement.selector, "INVALID_INPUT"));
        deliver(FORWARDER, metadataFor(OWNER), report("INVALID_INPUT", 0));
    }

    function test_lookalikeResultStringReverts() public {
        // "SETTLE " (trailing space) and "settle" must not be accepted.
        vm.expectRevert(abi.encodeWithSelector(SealedBidReceiver.NotASettlement.selector, "settle"));
        deliver(FORWARDER, metadataFor(OWNER), report("settle", 105_000_000));
    }

    // ── who may deliver ────────────────────────────────────────────────────

    function test_onlyForwarderMayDeliver() public {
        vm.expectRevert(abi.encodeWithSelector(SealedBidReceiver.UnauthorizedSender.selector, STRANGER));
        deliver(STRANGER, metadataFor(OWNER), report("SETTLE", 105_000_000));
    }

    function test_wrongWorkflowOwnerRejected() public {
        vm.expectRevert(abi.encodeWithSelector(SealedBidReceiver.UnexpectedWorkflowOwner.selector, STRANGER));
        deliver(FORWARDER, metadataFor(STRANGER), report("SETTLE", 105_000_000));
    }

    function test_ownerCheckDisabledWhenZero() public {
        SealedBidReceiver open = new SealedBidReceiver(FORWARDER, address(0));
        vm.prank(FORWARDER);
        open.onReport(metadataFor(STRANGER), report("SETTLE", 105_000_000));
        assertEq(open.settlementCount(), 1);
    }

    function test_shortMetadataRejected() public {
        vm.expectRevert(SealedBidReceiver.MalformedMetadata.selector);
        deliver(FORWARDER, hex"0102", report("SETTLE", 105_000_000));
    }

    function test_zeroForwarderRejectedAtDeploy() public {
        vm.expectRevert(SealedBidReceiver.InvalidForwarder.selector);
        new SealedBidReceiver(address(0), OWNER);
    }

    // ── ERC-165 ────────────────────────────────────────────────────────────

    function test_supportsIReceiverAndIERC165() public view {
        assertTrue(receiver.supportsInterface(type(IReceiver).interfaceId));
        assertTrue(receiver.supportsInterface(type(IERC165).interfaceId));
        assertFalse(receiver.supportsInterface(0xffffffff));
    }

    // ── the report never carries a reserve price ───────────────────────────

    function testFuzz_onlyMidpointIsStored(uint128 buyerMaxMicro, uint128 sellerMinMicro) public {
        vm.assume(sellerMinMicro <= buyerMaxMicro);
        uint256 clearing = (uint256(buyerMaxMicro) + uint256(sellerMinMicro)) / 2;
        vm.assume(clearing > 0);
        bytes32 id = keccak256(abi.encode(buyerMaxMicro, sellerMinMicro));
        bytes memory rep = abi.encode("SETTLE", clearing, "fuzz", commitA, commitB, id);
        deliver(FORWARDER, metadataFor(OWNER), rep);
        // Only what the enclave chose to publish is on chain; the receiver holds no other field.
        assertEq(receiver.getSettlement(id).clearingPriceMicro, clearing);
    }
}
