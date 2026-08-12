import gzip

import pytest

from ..utils.osb_proposal_db import (
    MAX_PROPOSAL_BYTES,
    OsbProposalIntegrityError,
    _assert_bounded,
    _decompress_limited,
)


def test_protocol_scale_node_envelope_remains_bounded():
    # Root + 59 child arrays of 9,999 scalar nodes remains below 600,000.
    _assert_bounded([[None] * 9_999 for _ in range(59)])
    with pytest.raises(OsbProposalIntegrityError, match="NODE_LIMIT_EXCEEDED"):
        _assert_bounded([[None] * 9_999 for _ in range(60)])


def test_decompressed_proposal_limit_is_32_mib_and_fails_closed():
    assert MAX_PROPOSAL_BYTES == 32 * 1024 * 1024
    oversized = gzip.compress(b"x" * (MAX_PROPOSAL_BYTES + 1))
    with pytest.raises(
        OsbProposalIntegrityError, match="DECOMPRESSED_BYTE_LIMIT_EXCEEDED"
    ):
        _decompress_limited(oversized)
