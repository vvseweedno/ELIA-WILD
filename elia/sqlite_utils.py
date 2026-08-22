from __future__ import annotations

import sqlite3


def inserted_row_id(cursor: sqlite3.Cursor, *, operation: str) -> int:
    """Return the row id produced by an INSERT, failing if SQLite produced none.

    ``sqlite3.Cursor.lastrowid`` is optional at the DB-API boundary, and a fresh
    non-INSERT cursor may expose ``0`` on some supported Python/SQLite builds.
    Persistence code must not allow either sentinel to leak into positive domain
    identifiers, so every caller names the failed operation and gets a deterministic
    runtime error instead of a late ``TypeError``.
    """

    row_id = cursor.lastrowid
    if row_id is None or row_id < 1:
        raise RuntimeError(f"SQLite did not return a row id for {operation}")
    return row_id
