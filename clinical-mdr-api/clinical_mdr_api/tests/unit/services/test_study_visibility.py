from clinical_mdr_api.services.studies.study_visibility import (
    caller_identities,
    catalog_item_author,
    study_visible_to_user,
)


class _User:
    def __init__(self, *, oid, username, roles):
        self.oid = oid
        self.username = username
        self.email = f"{username}@example.com"
        self.name = username
        self.sub = oid
        self.roles = set(roles)

    def id(self):
        return self.oid

    def has_role(self, role):
        return role in self.roles


def test_admin_sees_every_study():
    admin = _User(oid="edc:2", username="staging-admin", roles={"Admin.Read"})
    assert study_visible_to_user(admin, "someone.else") is True
    assert study_visible_to_user(admin, None) is True


def test_investigator_only_sees_own_authored_studies():
    investigator = _User(
        oid="edc:7", username="iso_crit", roles={"Study.Read", "Library.Read"}
    )
    assert study_visible_to_user(investigator, "edc:7") is True
    assert study_visible_to_user(investigator, "iso_crit") is True
    assert study_visible_to_user(investigator, "unknown-user") is False
    assert study_visible_to_user(investigator, None) is False


def test_assigned_study_uid_is_visible_even_when_not_author():
    investigator = _User(
        oid="edc:23", username="sandbox_ora", roles={"Study.Read", "Library.Read"}
    )
    investigator.study_ids = {"Study_000017"}
    assert (
        study_visible_to_user(
            investigator, "staging-admin", study_uid="Study_000017"
        )
        is True
    )
    assert (
        study_visible_to_user(
            investigator, "staging-admin", study_uid="Study_000001"
        )
        is False
    )
    assert study_visible_to_user(investigator, "staging-admin") is False


def test_missing_auth_context_stays_unrestricted():
    assert study_visible_to_user(None, "unknown-user") is True


def test_assert_study_uid_visible_skips_admin_and_missing_user(monkeypatch):
    from clinical_mdr_api.services.studies import study_visibility as vis
    from common.exceptions import NotFoundException

    called = {"repo": False}

    monkeypatch.setattr(vis, "_request_user", lambda: None)
    vis.assert_study_uid_visible("Study_000001")

    admin = _User(oid="edc:2", username="staging-admin", roles={"Admin.Read"})
    monkeypatch.setattr(vis, "_request_user", lambda: admin)
    vis.assert_study_uid_visible("Study_000001")
    assert called["repo"] is False

    investigator = _User(
        oid="edc:10", username="iso_nest", roles={"Study.Read"}
    )

    class _Study:
        current_metadata = type(
            "M",
            (),
            {"version_metadata": type("V", (), {"version_author": "someone.else"})()},
        )()

    class _InvRepos:
        class study_definition_repository:
            @staticmethod
            def find_by_uid(uid):
                called["repo"] = True
                return _Study()

        def close(self):
            return None

    monkeypatch.setattr(vis, "_request_user", lambda: investigator)
    monkeypatch.setattr(
        "clinical_mdr_api.services._meta_repository.MetaRepository",
        _InvRepos,
    )
    try:
        vis.assert_study_uid_visible("Study_000001")
        raise AssertionError("expected NotFoundException")
    except NotFoundException:
        pass
    assert called["repo"] is True


def test_catalog_admin_helper(monkeypatch):
    from clinical_mdr_api.services.studies import study_visibility as vis

    monkeypatch.setattr(vis, "_request_user", lambda: None)
    assert vis.caller_is_catalog_admin() is True
    monkeypatch.setattr(
        vis,
        "_request_user",
        lambda: _User(oid="edc:2", username="staging-admin", roles={"Admin.Read"}),
    )
    assert vis.caller_is_catalog_admin() is True
    monkeypatch.setattr(
        vis,
        "_request_user",
        lambda: _User(oid="edc:11", username="iso_nest", roles={"Study.Read"}),
    )
    assert vis.caller_is_catalog_admin() is False


def test_catalog_item_author_reads_compact_and_dict_shapes():
    class Version:
        version_author = "edc:7"

    class Meta:
        version_metadata = Version()
        ver_metadata = Version()

    class Item:
        current_metadata = Meta()

    assert catalog_item_author(Item()) == "edc:7"
    assert catalog_item_author({"version_author": "iso_crit"}) == "iso_crit"
    assert catalog_item_author({"version_author_id": "edc:7"}) == "edc:7"
    assert "edc:7" in caller_identities(
        _User(oid="edc:7", username="iso_crit", roles=set())
    )
