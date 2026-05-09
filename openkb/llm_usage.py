from __future__ import annotations

import contextlib
import contextvars
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LlmUsageContext:
    kb_dir: Path
    feature: str


_ACTIVE_CONTEXT: contextvars.ContextVar[LlmUsageContext | None] = contextvars.ContextVar(
    "openkb_llm_usage_context",
    default=None,
)

_DEFAULT_PAGE_SIZE = 50
_MAX_PAGE_SIZE = 200
_MAX_STORED_ROWS = 200


def usage_db_path(kb_dir: Path) -> Path:
    return Path(kb_dir) / ".openkb" / "llm-usage" / "usage.db"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS llm_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            feature TEXT NOT NULL,
            model TEXT NOT NULL,
            wire_api TEXT NOT NULL,
            base_url TEXT NOT NULL,
            status TEXT NOT NULL,
            duration_ms INTEGER NOT NULL,
            error TEXT NOT NULL,
            input_payload TEXT NOT NULL,
            output_payload TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_llm_usage_created_at ON llm_usage(created_at DESC)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_llm_usage_feature ON llm_usage(feature)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_llm_usage_model ON llm_usage(model)"
    )
    connection.commit()


def _connect(kb_dir: Path) -> sqlite3.Connection:
    path = usage_db_path(Path(kb_dir).resolve())
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    _ensure_schema(connection)
    return connection


def _connect_if_exists(kb_dir: Path) -> sqlite3.Connection | None:
    path = usage_db_path(Path(kb_dir).resolve())
    if not path.exists():
        return None
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    _ensure_schema(connection)
    return connection


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _json_value(value: str) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _search_clause(q: str) -> tuple[str, list[str]]:
    query = str(q or "").strip()
    if not query:
        return "", []
    pattern = f"%{query}%"
    return (
        (
            "WHERE feature LIKE ? COLLATE NOCASE "
            "OR model LIKE ? COLLATE NOCASE "
            "OR status LIKE ? COLLATE NOCASE "
            "OR error LIKE ? COLLATE NOCASE"
        ),
        [pattern, pattern, pattern, pattern],
    )


def _pagination(page: int, page_size: int) -> tuple[int, int]:
    safe_page = max(int(page or 1), 1)
    safe_page_size = min(max(int(page_size or _DEFAULT_PAGE_SIZE), 1), _MAX_PAGE_SIZE)
    return safe_page, safe_page_size


@contextlib.contextmanager
def llm_usage_context(kb_dir: Path, feature: str):
    feature_name = str(feature or "").strip()
    if not feature_name:
        yield None
        return
    context = LlmUsageContext(Path(kb_dir).resolve(), feature_name)
    token = _ACTIVE_CONTEXT.set(context)
    try:
        yield context
    finally:
        _ACTIVE_CONTEXT.reset(token)


def get_llm_usage_context() -> LlmUsageContext | None:
    return _ACTIVE_CONTEXT.get()


def record_usage(
    *,
    kb_dir: Path,
    feature: str,
    model: str,
    wire_api: str,
    base_url: str,
    status: str,
    duration_ms: int,
    error: str = "",
    input_payload: Any = None,
    output_payload: Any = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    payload = {
        "created_at": created_at or _now(),
        "feature": str(feature or "").strip(),
        "model": str(model or "").strip(),
        "wire_api": str(wire_api or "").strip(),
        "base_url": str(base_url or "").strip(),
        "status": str(status or "").strip(),
        "duration_ms": max(int(duration_ms or 0), 0),
        "error": str(error or "").strip(),
        "input_payload": _json_text(input_payload),
        "output_payload": _json_text(output_payload),
    }
    connection = _connect(kb_dir)
    try:
        cursor = connection.execute(
            """
            INSERT INTO llm_usage (
                created_at,
                feature,
                model,
                wire_api,
                base_url,
                status,
                duration_ms,
                error,
                input_payload,
                output_payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["created_at"],
                payload["feature"],
                payload["model"],
                payload["wire_api"],
                payload["base_url"],
                payload["status"],
                payload["duration_ms"],
                payload["error"],
                payload["input_payload"],
                payload["output_payload"],
            ),
        )
        connection.execute(
            """
            DELETE FROM llm_usage
            WHERE id NOT IN (
                SELECT id
                FROM llm_usage
                ORDER BY created_at DESC, id DESC
                LIMIT ?
            )
            """,
            (_MAX_STORED_ROWS,),
        )
        connection.commit()
        payload["id"] = int(cursor.lastrowid)
    finally:
        connection.close()
    return payload


def _row_to_item(row: sqlite3.Row, *, include_payloads: bool) -> dict[str, Any]:
    item = {
        "id": int(row["id"]),
        "created_at": str(row["created_at"]),
        "feature": str(row["feature"]),
        "model": str(row["model"]),
        "wire_api": str(row["wire_api"]),
        "base_url": str(row["base_url"]),
        "status": str(row["status"]),
        "duration_ms": int(row["duration_ms"]),
        "error": str(row["error"]),
    }
    if include_payloads:
        item["input_payload"] = _json_value(str(row["input_payload"]))
        item["output_payload"] = _json_value(str(row["output_payload"]))
    return item


def list_usage(
    kb_dir: Path,
    *,
    q: str = "",
    page: int = 1,
    page_size: int = _DEFAULT_PAGE_SIZE,
) -> dict[str, Any]:
    safe_page, safe_page_size = _pagination(page, page_size)
    connection = _connect_if_exists(kb_dir)
    if connection is None:
        return {
            "items": [],
            "total": 0,
            "page": safe_page,
            "page_size": safe_page_size,
            "pages": 1,
            "start": 0,
            "end": 0,
        }
    try:
        where_clause, params = _search_clause(q)
        total = int(
            connection.execute(
                f"SELECT COUNT(*) FROM llm_usage {where_clause}",
                params,
            ).fetchone()[0]
        )
        pages = max((total + safe_page_size - 1) // safe_page_size, 1)
        safe_page = min(safe_page, pages)
        offset = (safe_page - 1) * safe_page_size
        rows = connection.execute(
            f"""
            SELECT
                id,
                created_at,
                feature,
                model,
                wire_api,
                base_url,
                status,
                duration_ms,
                error
            FROM llm_usage
            {where_clause}
            ORDER BY created_at DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            [*params, safe_page_size, offset],
        ).fetchall()
    finally:
        connection.close()
    start = offset + 1 if total else 0
    end = min(offset + safe_page_size, total)
    return {
        "items": [_row_to_item(row, include_payloads=False) for row in rows],
        "total": total,
        "page": safe_page,
        "page_size": safe_page_size,
        "pages": pages,
        "start": start,
        "end": end,
    }


def export_usage(kb_dir: Path, *, q: str = "") -> list[dict[str, Any]]:
    connection = _connect_if_exists(kb_dir)
    if connection is None:
        return []
    try:
        where_clause, params = _search_clause(q)
        rows = connection.execute(
            f"""
            SELECT
                id,
                created_at,
                feature,
                model,
                wire_api,
                base_url,
                status,
                duration_ms,
                error,
                input_payload,
                output_payload
            FROM llm_usage
            {where_clause}
            ORDER BY created_at DESC, id DESC
            """,
            params,
        ).fetchall()
    finally:
        connection.close()
    return [_row_to_item(row, include_payloads=True) for row in rows]
