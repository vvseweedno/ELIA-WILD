from __future__ import annotations

import sqlite3

import pytest

from elia.sqlite_utils import inserted_row_id


def test_inserted_row_id_returns_successful_insert_identifier() -> None:
    with sqlite3.connect(":memory:") as connection:
        connection.execute("CREATE TABLE events(id INTEGER PRIMARY KEY, payload TEXT)")
        cursor = connection.execute("INSERT INTO events(payload) VALUES (?)", ("event",))

    assert inserted_row_id(cursor, operation="test event insert") == 1


def test_inserted_row_id_rejects_cursor_without_insert_identifier() -> None:
    with sqlite3.connect(":memory:") as connection:
        cursor = connection.cursor()
        cursor.execute("SELECT 1")

    with pytest.raises(RuntimeError, match="test event insert"):
        inserted_row_id(cursor, operation="test event insert")
