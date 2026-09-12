import logging
import os
import urllib.parse
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Generic, Self, TypeVar

from fastapi import Query, Request
from neomodel import db
from pydantic import BaseModel, Field
from requests.utils import requote_uri

from common.config import settings
from common.exceptions import ValidationException
from common.utils import filter_sort_valid_keys_re, get_db_result_as_dict

T = TypeVar("T")


class SortByType(Enum):
    STRING = "string"
    NUMBER = "number"


class PaginatedResponse(BaseModel, Generic[T]):
    """
    Paginated response model
    """

    self: Annotated[
        str, Field(description="Pagination link pointing to the current page")
    ]
    prev: Annotated[
        str, Field(description="Pagination link pointing to the previous page")
    ]
    next: Annotated[str, Field(description="Pagination link pointing to the next page")]
    total: Annotated[int, Field(description="Total number of items")] = 0
    items: Annotated[list[T], Field(description="List of items")]

    @classmethod
    def from_input(
        cls,
        request: Request,
        sort_by: str,
        sort_order: str,
        page_size: int,
        page_number: int,
        items: list[T],
        query_param_names: list[str] | None = None,
        total: int = 0,
    ) -> Self:
        path = request.url.path

        # Extract query parameters not related to sorting/pagination from the request
        query_params = ""
        if query_param_names:
            for query_param_name in query_param_names:
                query_param_val = request.query_params.get(query_param_name)
                if query_param_val:
                    query_params = (
                        f"{query_params}{query_param_name}={query_param_val}&"
                    )
        query_params = requote_uri(query_params)

        prev_page_number = page_number - 1 if page_number > 1 else 1

        self_link = f"{path}?{query_params}sort_by={sort_by}&sort_order={sort_order}&page_size={page_size}&page_number={page_number}"
        prev_link = f"{path}?{query_params}sort_by={sort_by}&sort_order={sort_order}&page_size={page_size}&page_number={prev_page_number}"
        next_link = f"{path}?{query_params}sort_by={sort_by}&sort_order={sort_order}&page_size={page_size}&page_number={page_number + 1}"

        # pylint: disable=kwarg-superseded-by-positional-arg
        return cls(
            self=urlencode_link(self_link),
            prev=urlencode_link(prev_link),
            next=urlencode_link(next_link),
            items=items,
            total=total,
        )


def query(
    cypher_query,
    params: dict[Any, Any] | None = None,
    handle_unique: bool = True,
    retry_on_session_expire: bool = False,
    resolve_objects: bool = False,
    to_dict_list: bool = True,
):
    """
    Wraps `db.cypher_query()`

    Returns:
    list[dict] | tuple: If `to_dict_list` is True, returns a list of dictionaries representing the query results.
                        If `to_dict_list` is False, returns a tuple containing the rows and columns from the query.
    """
    if params is None:
        params = {}

    try:
        rows, columns = db.cypher_query(
            query=cypher_query,
            params=params,
            handle_unique=handle_unique,
            retry_on_session_expire=retry_on_session_expire,
            resolve_objects=resolve_objects,
        )
    except Exception as e:
        raise ValidationException(msg=f"Database query failed: {e.message}") from e

    if to_dict_list:
        return [get_db_result_as_dict(row, columns) for row in rows]

    return rows, columns


def urlencode_link(link: str) -> str:
    """URL encodes a link"""

    url = urllib.parse.urlparse(link)
    query_params = urllib.parse.parse_qs(url.query, keep_blank_values=True)

    url = url._replace(query=urllib.parse.urlencode(query_params, True))
    return urllib.parse.urlunparse(url)


def db_pagination_clause(page_size: int, page_number: int) -> str:
    # Ensure Cypher injection would not be possible even if values weren't integer types
    if not isinstance(page_size, int) or not isinstance(page_number, int):
        raise TypeError("Expected page_size and page_number to be integers")

    limit = f"LIMIT {page_size}" if page_size > 0 else ""
    return f"SKIP {page_number - 1} * {page_size} {limit}"


def db_sort_clause(
    sort_by: str,
    sort_order: str = "ASC",
    sort_by_type: SortByType = SortByType.STRING,
    secondary_sort_fields: str = "uid",
) -> str:
    # Ensure Cypher injection would not be exploitable even if sort_by keys were not checked
    if not filter_sort_valid_keys_re.fullmatch(sort_by):
        raise ValidationException(msg=f"Invalid sorting key: {sort_by}")

    if sort_by_type == SortByType.NUMBER:
        primary_sort = f"toFloat({sort_by}) {sort_order}"
    else:
        primary_sort = f"toLower(toString({sort_by})) {sort_order}"

    # Add hash of all relevant properties as secondary sort for consistent ordering
    if secondary_sort_fields:
        secondary_sort = f"apoc.util.md5([{secondary_sort_fields}]) ASC"
        return f"ORDER BY {primary_sort}, {secondary_sort}"

    return f"ORDER BY {primary_sort}"


def get_api_version() -> str:
    version_path = os.path.join("./extensions", "apiVersion")
    with open(version_path, "r", encoding="utf-8") as file:
        return file.read().strip()


# Reusable Query parameter for page_number with maximum constraint
PAGE_NUMBER_QUERY = Query(
    ge=0,
    le=settings.max_page_number,
)

# Reusable Query parameter for page_size with maximum constraint (allows page_size=0 for "all rows")
PAGE_SIZE_QUERY = Query(
    ge=0,
    le=settings.max_page_size,
)


class Logger:

    def __init__(self, name: str = __name__):
        self.full_log: list[str] = []
        self.log = logging.getLogger(name)

    @classmethod
    def format_message(cls, msg: str, level: str = "INFO") -> str:
        return f"[{datetime.now(timezone.utc)}] {level} {msg}"

    def debug(self, msg: str) -> None:
        self.log.debug(msg)
        self.full_log.append(self.format_message(msg, "DEBUG"))

    def info(self, msg: str) -> None:
        self.log.info(msg)
        self.full_log.append(self.format_message(msg, "INFO"))

    def warning(self, msg: str) -> None:
        self.log.warning(msg)
        self.full_log.append(self.format_message(msg, "WARNING"))

    def error(self, msg: str) -> None:
        self.log.error(msg)
        self.full_log.append(self.format_message(msg, "ERROR"))
