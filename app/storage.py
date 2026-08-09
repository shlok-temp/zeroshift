"""Share-link storage. Falls back to memory when no database is configured."""

from __future__ import annotations

import json
import os
import secrets
from typing import Any

import psycopg
from psycopg.rows import dict_row

SHARE_ID_BYTES = 6
memory_store: dict[str, dict[str, Any]] = {}


def database_url() -> str | None:
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    host = os.getenv("DB_HOST")
    if not host:
        return None
    user = os.getenv("DB_USER", "postgres")
    password = os.getenv("DB_PASS", "")
    name = os.getenv("DB_NAME", "postgres")
    port = os.getenv("DB_PORT", "5432")
    return f"postgresql://{user}:{password}@{host}:{port}/{name}"


def connect():
    url = database_url()
    return psycopg.connect(url, row_factory=dict_row) if url else None


def init_schema() -> bool:
    connection = connect()
    if not connection:
        return False
    with connection, connection.cursor() as cursor:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS migrations (
                id TEXT PRIMARY KEY,
                compose TEXT NOT NULL,
                project_name TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    return True


def new_id() -> str:
    return secrets.token_urlsafe(SHARE_ID_BYTES)


def save(compose: str, project_name: str) -> str:
    share_id = new_id()
    connection = connect()
    if not connection:
        memory_store[share_id] = {"compose": compose, "project_name": project_name}
        return share_id
    with connection, connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO migrations (id, compose, project_name) VALUES (%s, %s, %s)",
            (share_id, compose, project_name),
        )
    return share_id


def load(share_id: str) -> dict[str, Any] | None:
    connection = connect()
    if not connection:
        return memory_store.get(share_id)
    with connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT compose, project_name FROM migrations WHERE id = %s", (share_id,)
        )
        return cursor.fetchone()
