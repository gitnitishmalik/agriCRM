"""
Cursor pagination, with an ordering that exists.

Doc 11 §1 puts every list endpoint on cursor pagination: offset pagination
re-scans and re-sorts the whole prefix for every page, and on an actively
written table it also skips and repeats rows as the offsets shift underneath
the reader. A field agent paging through a district while an import runs would
see neither a stable list nor a complete one.

DRF's `CursorPagination` defaults to ordering on `-created`, which no model in
this system has, so the base class here supplies one that does — and the
reference tables, which have no timestamps at all, get the id-ordered variant.

`count` is deliberately absent from cursor-paginated responses. Doc 11 §1
describes it as an estimate above 10,000 rows for exactly the reason it is not
computed here: an exact count over a partitioned table is a sequential scan,
paid on every page load, to render a number nobody acts on.
"""

from __future__ import annotations

from rest_framework.pagination import CursorPagination


class TimelineCursorPagination(CursorPagination):
    """Newest first. The default for anything with a creation timestamp."""

    page_size = 50
    max_page_size = 200
    page_size_query_param = "limit"
    ordering = "-created_at"


class ReferenceCursorPagination(CursorPagination):
    """
    For `ref` tables, which carry no timestamps.

    Ordered by primary key rather than name: cursor pagination needs a
    total order, and village names are unique only within a block.
    """

    page_size = 100
    max_page_size = 500
    page_size_query_param = "limit"
    ordering = "id"
