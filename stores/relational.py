#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""关系库适配器：PostgreSQL 优先，缺失时降级 SQLite（标准库，零新增依赖）。

只存审计元数据 / 任务状态 / 来源评估，**绝不存 JD 原文、简历、OCR 文本或 PII**。
占位符统一写 `?`，PostgreSQL 分支在执行前替换为 `%s`，保证两条链路 SQL 等价。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from secure_config import cfg, secret

from .schema import POSTGRES_DDL, SQLITE_DDL

ROOT = Path(__file__).resolve().parent.parent
DB_FILE = ROOT / ".stores" / "relational.db"

_LOCK = threading.Lock()
_CONN: Any = None
_BACKEND = "uninitialized"
_FALLBACK_REASON = ""
_TABLES: list[str] = []


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dsn_parts() -> dict[str, str]:
    return {
        "host": cfg.get("POSTGRES_HOST"), "port": cfg.get("POSTGRES_PORT"),
        "dbname": cfg.get("POSTGRES_DB"), "user": cfg.get("POSTGRES_USER"),
        "sslmode": cfg.get("POSTGRES_SSLMODE"),
        "password": secret("POSTGRES_PASSWORD") or "",
    }


def _init_postgres() -> Any:
    global _FALLBACK_REASON
    try:
        import psycopg  # type: ignore
    except Exception as exc:
        _FALLBACK_REASON = "psycopg 未安装（%s）" % type(exc).__name__
        return None
    p = _dsn_parts()
    try:
        conn = psycopg.connect(connect_timeout=3, **p)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(POSTGRES_DDL)
        return conn
    except Exception as exc:
        _FALLBACK_REASON = "PostgreSQL 连接失败（%s）" % type(exc).__name__
        return None


def _init_sqlite() -> Any:
    global _FALLBACK_REASON
    try:
        DB_FILE.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_FILE), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.executescript(SQLITE_DDL)
        conn.commit()
        return conn
    except Exception as exc:
        _FALLBACK_REASON = "SQLite 降级也失败（%s）" % type(exc).__name__
        return None


def connect(force: bool = False) -> Any:
    global _CONN, _BACKEND, _TABLES
    with _LOCK:
        if _CONN is not None and not force:
            return _CONN
        conn = _init_postgres()
        backend = "postgresql"
        if conn is None:
            conn = _init_sqlite()
            backend = "sqlite-fallback"
        _CONN, _BACKEND = conn, backend if conn else "unavailable"
        try:
            cur = _CONN.execute(
                "SELECT name FROM sqlite_master WHERE type='table'" if backend == "sqlite-fallback"
                else "SELECT tablename AS name FROM pg_tables WHERE schemaname='public'")
            rows = cur.fetchall()
            _TABLES = [r[0] if isinstance(r, (tuple, list)) else r["name"] for r in rows]
            _TABLES = [t for t in _TABLES if str(t).startswith("zhiyin_")]
        except Exception:
            _TABLES = []
        return _CONN


def _execute(sql: str, params: tuple = ()) -> Any:
    conn = connect()
    if conn is None:
        return None
    if _BACKEND == "postgresql":
        sql = sql.replace("?", "%s")
    with _LOCK:
        return conn.execute(sql, params)


def record_event(kind: str, source_id: str = "", grade: str = "",
                 detail: dict[str, Any] | None = None) -> bool:
    """写一条审计事件。detail 会被强制 JSON 化，调用方不得塞入原文或 PII。"""
    try:
        _execute(
            "INSERT INTO zhiyin_events (ts, kind, source_id, grade, detail) VALUES (?,?,?,?,?)",
            (_now(), kind[:64], source_id[:48], grade[:8],
             json.dumps(detail or {}, ensure_ascii=False)),
        )
        if _BACKEND == "sqlite-fallback" and _CONN is not None:
            _CONN.commit()
        return True
    except Exception:
        return False


def recent(limit: int = 20) -> list[dict[str, Any]]:
    try:
        cur = _execute("SELECT ts, kind, source_id, grade, detail FROM zhiyin_events "
                       "ORDER BY ts DESC LIMIT ?", (min(max(limit, 1), 100),))
        if cur is None:
            return []
        rows = cur.fetchall()
        out = []
        for r in rows:
            # PostgreSQL 返回 datetime / JSONB(dict)，SQLite 返回 str；
            # 统一成可直接 JSON 序列化的字符串，保证两条后端接口等价。
            ts, detail = r[0], r[4]
            out.append({
                "ts": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
                "kind": r[1], "source_id": r[2], "grade": r[3],
                "detail": (json.dumps(detail, ensure_ascii=False)
                           if isinstance(detail, (dict, list)) else str(detail)),
            })
        return out
    except Exception:
        return []


def upsert_task(task_id: str, name: str, state: str, backend: str,
                detail: dict[str, Any] | None = None) -> bool:
    try:
        _execute(
            "INSERT INTO zhiyin_tasks (id, name, state, backend, created_at, finished_at, detail) "
            "VALUES (?,?,?,?,?,?,?) "
            + ("ON CONFLICT (id) DO UPDATE SET state=EXCLUDED.state, "
               "finished_at=EXCLUDED.finished_at, detail=EXCLUDED.detail"
               if _BACKEND == "postgresql" else "ON CONFLICT(id) DO UPDATE SET "
               "state=excluded.state, finished_at=excluded.finished_at, detail=excluded.detail"),
            (task_id[:64], name[:64], state[:24], backend[:32], _now(),
             _now() if state in ("SUCCESS", "FAILURE") else None,
             json.dumps(detail or {}, ensure_ascii=False)),
        )
        if _BACKEND == "sqlite-fallback" and _CONN is not None:
            _CONN.commit()
        return True
    except Exception:
        return False


def status() -> dict[str, Any]:
    conn = connect()
    return {
        "backend": _BACKEND,
        "connected": conn is not None,
        "tables": _TABLES,
        "fallback_reason": _FALLBACK_REASON,
        "host": cfg.get("POSTGRES_HOST") if _BACKEND == "postgresql" else "(local file)",
        "password_configured": bool(secret("POSTGRES_PASSWORD")),
        "data_policy": "仅审计元数据 / 任务状态；不存 JD 原文、简历、OCR 文本或 PII",
        "sqlite_path": ".stores/relational.db (gitignored)" if _BACKEND == "sqlite-fallback" else None,
        "ddl": "deploy/sql/001_init.sql",
    }
