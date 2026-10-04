"""A term may specialize several parents; re-adding one changes nothing.

add_parent() called get_or_none() on the relationship for every type. For a term
that already specialized two parents (2.10's datetime specializes string and
integer) that raised MultipleNodesReturned, so re-running the sponsor datatype
import, or adding a third specialization, failed. The importer posts every parent
on every run, so re-adding a specialization the term already has stays a no-op,
as it was for a single parent. Parent type and subtype stay single-valued.
"""

import types
from unittest import mock

import pytest

from clinical_mdr_api.domain_repositories.controlled_terminologies import (
    ct_term_generic_repository as repository_module,
)
from clinical_mdr_api.domains.controlled_terminologies.utils import TermParentType
from common.exceptions import AlreadyExistsException


class MultipleNodesReturned(Exception):
    """Stands in for neomodel's error when get_or_none() matches several nodes."""


class FakeRelationship:
    def __init__(self, connected=()):
        self.connected = list(connected)

    def get_or_none(self):
        if len(self.connected) > 1:
            raise MultipleNodesReturned()
        return self.connected[0] if self.connected else None

    def is_connected(self, node):
        return node in self.connected

    def connect(self, node):
        self.connected.append(node)


def term(uid, specializes=(), parent_types=()):
    return types.SimpleNamespace(
        uid=uid,
        is_specialization_of=FakeRelationship(specializes),
        has_parent_type=FakeRelationship(parent_types),
        has_parent_subtype=FakeRelationship(),
        has_predecessor=FakeRelationship(),
    )


def add_parent(terms, term_uid, parent_uid, relationship_type):
    def get_or_none(uid):
        # CTTermRoot.nodes.get_or_none is called with the keyword uid=
        return terms.get(uid)

    fake_root = types.SimpleNamespace(
        nodes=types.SimpleNamespace(get_or_none=get_or_none)
    )
    with mock.patch.object(repository_module, "CTTermRoot", fake_root):
        repository_module.CTTermGenericRepository.add_parent(
            types.SimpleNamespace(),
            term_uid=term_uid,
            parent_uid=parent_uid,
            relationship_type=relationship_type,
        )


def test_a_term_specializing_two_parents_can_specialize_a_third():
    string, integer, text = term("string"), term("integer"), term("text")
    datetime = term("datetime", specializes=[string, integer])
    terms = {t.uid: t for t in (string, integer, text, datetime)}
    add_parent(terms, "datetime", "text", TermParentType.SPECIALIZATION)
    assert datetime.is_specialization_of.connected == [string, integer, text]


def test_re_adding_a_specialization_parent_changes_nothing():
    string, integer = term("string"), term("integer")
    datetime = term("datetime", specializes=[string, integer])
    terms = {t.uid: t for t in (string, integer, datetime)}
    add_parent(terms, "datetime", "integer", TermParentType.SPECIALIZATION)
    assert datetime.is_specialization_of.connected == [string, integer]


def test_a_parent_type_stays_single_valued():
    first, second = term("first"), term("second")
    child = term("child", parent_types=[first])
    with pytest.raises(AlreadyExistsException):
        add_parent(
            {"first": first, "second": second, "child": child},
            "child",
            "second",
            TermParentType.PARENT_TYPE,
        )
    assert child.has_parent_type.connected == [first]
