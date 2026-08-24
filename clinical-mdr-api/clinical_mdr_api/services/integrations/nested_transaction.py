"""Calling a `@db.transaction`-decorated read from inside an open transaction.

WHY THIS EXISTS
---------------
neomodel refuses to nest transactions: entering `db.transaction` while one is
already open raises ``SystemError: Transaction in progress``. Most of this API
never notices, because a decorated service read is normally reached from a
router that opens nothing of its own.

The governed platform commands are the exception. Every one of them runs its
whole body inside a single serializable transaction
(``Neo4jOsbPlatformCommandStore.serializable`` → ``with db.transaction``), so a
command that reaches ANY ``@db.transaction``-decorated service read dies with an
unhandled ``SystemError`` — which, before the exception handlers were repaired,
surfaced as a bare 500 with nothing naming the cause. That is exactly how the
governed candidate round-trip failed: on a read, not a write
(``StudyStandardVersionService.get_standard_versions_in_study``).

THE SHAPE OF THE SWEEP (recorded so the next person does not redo it)
---------------------------------------------------------------------
269 methods in this API carry ``@db.transaction``. Twelve of them are called
from ``services/integrations``. Eleven of those twelve sit inside
``StudyAuthorityService.get_snapshot`` and ``EdcExportService.build_bundle``,
both of which are reached ONLY from plain GET routers that open no transaction
of their own — so they are correct as they stand and must not be changed.

The single call site inside a command transaction today is
``MappingContextService._load_standard_versions``, and it uses this helper.

The point of putting the pattern here is the NEXT command family: any governed
command that grows a study-data read will hit this the first time it runs, and
the fix should be one call rather than a thirty-line comment copied a fifth time.

WHY NOT MAKE ``db.transaction`` RE-ENTRANT INSTEAD
--------------------------------------------------
That would change transaction semantics for all 269 sites, including writes,
where silently joining an ambient transaction changes what a rollback undoes.
Narrowing the exception to reads that explicitly ask for it keeps the blast
radius at the call site.
"""

from typing import Any, Callable, TypeVar

from neomodel import db

T = TypeVar("T")

__all__ = ["call_in_ambient_transaction"]


def call_in_ambient_transaction(
    bound_method: Callable[..., T],
    *args: Any,
    **kwargs: Any,
) -> T:
    """Call a ``@db.transaction``-decorated bound method, joining any open transaction.

    When no transaction is open the method is called normally and opens its own,
    so standalone callers are completely unaffected.

    When one IS open, the UNDECORATED function is called instead
    (``functools.wraps`` keeps it on ``__wrapped__``) and the surrounding
    transaction provides the atomicity the decorator would have provided. The
    read is still atomic — it is inside a transaction, just not its own.

    :param bound_method: a bound method whose function is ``@db.transaction``-decorated.
        A plain undecorated callable is passed through unchanged, so this is safe
        to apply defensively at a site whose callee may lose the decorator later.
    """
    inner = getattr(bound_method, "__wrapped__", None)
    receiver = getattr(bound_method, "__self__", None)

    # `__wrapped__` is the plain function, so the receiver has to be re-supplied.
    # Without a receiver there is nothing to call it on and the decorated path is
    # the only honest option.
    def _call_inner() -> T:
        if receiver is None:
            return inner(*args, **kwargs)  # type: ignore[misc]
        return inner(receiver, *args, **kwargs)  # type: ignore[misc]

    if inner is not None and _transaction_is_open():
        return _call_inner()

    try:
        return bound_method(*args, **kwargs)
    except SystemError:
        # The detection below reads a PRIVATE neomodel attribute, so a driver
        # upgrade that renames it would silently restore a hard 500 on every
        # governed command. Retrying undecorated on the ONE error nesting raises
        # costs nothing and cannot mask a real fault: if a transaction is
        # genuinely open, running the read inside it is what was wanted; if one
        # is not, this call does not raise SystemError in the first place.
        if inner is None:
            raise
        return _call_inner()


def _transaction_is_open() -> bool:
    """Whether neomodel currently holds an open transaction on the shared ``db``.

    ``_active_transaction`` is private. It is read here rather than inferred
    because there is no public equivalent, and the caller above treats a wrong
    answer as recoverable rather than fatal.
    """
    return getattr(db, "_active_transaction", None) is not None
