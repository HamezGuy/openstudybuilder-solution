import gzip

import pytest

from ..utils.osb_proposal_db import (
    MAX_PROPOSAL_BYTES,
    MAX_PROPOSAL_NODES,
    OsbProposalIntegrityError,
    _assert_bounded,
    _decompress_limited,
)


def test_protocol_scale_node_envelope_remains_bounded():
    assert MAX_PROPOSAL_NODES == 3_000_000
    # Root + 299 child arrays of 9,999 scalar nodes totals 2,990,001.
    _assert_bounded([[None] * 9_999 for _ in range(299)])
    with pytest.raises(OsbProposalIntegrityError, match="NODE_LIMIT_EXCEEDED"):
        # One more child array exceeds the reviewed ceiling by exactly one.
        _assert_bounded([[None] * 9_999 for _ in range(300)])


def test_decompressed_proposal_limit_is_128_mib_and_fails_closed():
    assert MAX_PROPOSAL_BYTES == 128 * 1024 * 1024
    oversized = gzip.compress(b"x" * (MAX_PROPOSAL_BYTES + 1))
    with pytest.raises(
        OsbProposalIntegrityError, match="DECOMPRESSED_BYTE_LIMIT_EXCEEDED"
    ):
        _decompress_limited(oversized)
