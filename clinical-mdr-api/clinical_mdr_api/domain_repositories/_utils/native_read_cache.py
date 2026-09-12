"""Context-local cache bypass for authoritative native repository reads.

The caller still owns the native transaction and dependency locks. This scope
neither grants authority nor makes several unlocked reads an atomic snapshot.
Ordinary reads keep their existing cache, keys, TTL and invalidation behavior.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from inspect import signature
from typing import Any, TypeVar, cast

from cachetools import cached

_Function = TypeVar("_Function", bound=Callable[..., Any])
_UNCACHED_NATIVE_READS: ContextVar[bool] = ContextVar(
    "uncached_native_reads", default=False
)


@contextmanager
def uncached_native_reads() -> Iterator[None]:
    """Read native state without consulting or populating repository caches."""
    token = _UNCACHED_NATIVE_READS.set(True)
    try:
        yield
    finally:
        _UNCACHED_NATIVE_READS.reset(token)


def native_read_cached(*args: Any, **kwargs: Any) -> Callable[[_Function], _Function]:
    """Adapt ``cachetools.cached`` for scoped reads and native ``for_update``.

    Binding the original signature recognizes positional ``for_update`` calls.
    An update read must execute its native locking path every time, including
    when a cache already contains an aggregate for that argument combination.
    Its nested reads also bypass caches, even if they do not take that flag.
    """
    cache_decorator = cached(*args, **kwargs)

    def decorate(function: _Function) -> _Function:
        function_signature = signature(function)
        update_parameter = function_signature.parameters.get("for_update")
        cached_function = cache_decorator(function)

        @wraps(function)
        def wrapper(*call_args: Any, **call_kwargs: Any) -> Any:
            if _UNCACHED_NATIVE_READS.get():
                return function(*call_args, **call_kwargs)

            if update_parameter is not None:
                bound = function_signature.bind(*call_args, **call_kwargs)
                if bound.arguments.get("for_update", update_parameter.default):
                    with uncached_native_reads():
                        return function(*call_args, **call_kwargs)

            return cached_function(*call_args, **call_kwargs)

        # Preserve cachetools' cache/key/lock/info helpers and its original
        # __wrapped__ target for existing diagnostics and explicit unwrapping.
        wrapper.__dict__.update(cached_function.__dict__)
        return cast(_Function, wrapper)

    return decorate
