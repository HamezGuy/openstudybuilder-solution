import json
import logging

from cachetools import TTLCache, cached
from neo4j.exceptions import Forbidden
from neomodel import db
from starlette_context import context

from common.auth.models import Auth, User

cache_persist_user = TTLCache(maxsize=1000, ttl=10)

log = logging.getLogger(__name__)


def auth() -> Auth:
    """Retrieves authentication-related information from the request context as Auth object."""

    return context.get("auth")


def user() -> User:
    """Retrieves user information as User object, member of the Auth object in the request context."""

    return auth().user


@cached(cache=cache_persist_user, key=lambda user_info: user_info.id())
def persist_user(user_info: User):
    """Persists user information in the database."""

    log.info("Persisting user %s", user_info)
    query = """
        MERGE (u:User {user_id: $id})
        ON CREATE
            SET u.created = datetime(),
                u.oid = $oid,
                u.azp = $azp,
                u.username = $username,
                u.name = $name,
                u.email = $email,
                u.roles = $roles,
                u.tenant_id = $tenant_id,
                u.study_ids = $study_ids,
                u.subject_type = $subject_type,
                u.issuer = $issuer,
                u.human_subject = $human_subject,
                u.service_actor = $service_actor,
                u.purpose = $purpose,
                u.capabilities = $capabilities,
                u.actor_chain_json = $actor_chain_json
        ON MATCH
            SET u.updated = datetime(),
                u.oid = $oid,
                u.azp = $azp,
                u.username = COALESCE($username, u.username),
                u.name = $name,
                u.email = $email,
                u.roles = $roles,
                u.tenant_id = $tenant_id,
                u.study_ids = $study_ids,
                u.subject_type = $subject_type,
                u.issuer = $issuer,
                u.human_subject = $human_subject,
                u.service_actor = $service_actor,
                u.purpose = $purpose,
                u.capabilities = $capabilities,
                u.actor_chain_json = $actor_chain_json
        """
    params = {
        "id": user_info.id(),
        "oid": user_info.oid,
        "azp": user_info.azp,
        "username": user_info.username,
        "name": user_info.name,
        "email": user_info.email,
        "roles": list(user_info.roles),
        "tenant_id": user_info.tenant_id or None,
        "study_ids": sorted(user_info.study_ids),
        "subject_type": user_info.subject_type,
        "issuer": user_info.issuer,
        "human_subject": user_info.human_subject or None,
        "service_actor": user_info.service_actor or None,
        "purpose": user_info.purpose or None,
        "capabilities": sorted(user_info.capabilities),
        "actor_chain_json": json.dumps(user_info.actor_chain, separators=(",", ":")),
    }
    try:
        db.cypher_query(
            query=query,
            params=params,
        )
    except Forbidden as e:
        log.error("Error persisting user %s: %s", user_info, e)


def clear_users_cache():
    cache_persist_user.clear()
    log.info("Users cache cleared")
