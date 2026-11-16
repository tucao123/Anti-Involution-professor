#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""职引者 ZHIYIN · 三库统一入口（PostgreSQL / Redis / Neo4j + 本地降级）

三个适配器彼此独立：任一服务不可用只影响自身，其余照常工作。
status() 如实报告每个库的实际后端，绝不把降级后端冒充成已连接真服务。

数据边界：三库只处理审计元数据、任务状态、公开聚合快照与脱敏技能字段。
C 级用户自带 JD / 简历 / OCR 原文一律零落盘，见 privacy.py / user_jd.py。
"""
from __future__ import annotations

from typing import Any

from . import cache, graph, relational
from .schema import GRAPH_MODEL, NEO4J_CYPHER, POSTGRES_DDL, REDIS_KEY_SPEC, SQLITE_DDL

__all__ = ["cache", "graph", "relational", "status", "sync_from_jd", "SCHEMA"]

SCHEMA = {
    "postgres_ddl": "deploy/sql/001_init.sql",
    "neo4j_cypher": "deploy/cypher/001_init.cypher",
    "redis_key_spec": REDIS_KEY_SPEC,
    "graph_model": GRAPH_MODEL,
}


def sync_from_jd(result: dict[str, Any]) -> dict[str, Any]:
    """把一次 C 级分析结果的结构化字段写入图谱，并记一条审计事件。
    调用方传入的必须是 user_jd.analyze / orchestration.run 的结果（已脱敏）。"""
    role = str(result.get("title", ""))[:64]
    skills = result.get("skills", []) or []
    written = graph.upsert_role_skills(role, skills)
    relational.record_event("graph_sync", source_id="user_jd", grade="C",
                            detail={"role": role, "skills": len(skills),
                                    "backend": written["backend"]})
    return {"ok": True, **written}


def status() -> dict[str, Any]:
    r, c, g = relational.status(), cache.status(), graph.status()
    real = {"postgresql": r["backend"] == "postgresql",
            "redis": c["backend"] == "redis",
            "neo4j": g["backend"] == "neo4j"}
    return {
        "ok": True,
        "postgresql": r,
        "redis": c,
        "neo4j": g,
        "all_real_services_connected": all(real.values()),
        "degraded": [name for name, ok in real.items() if not ok],
        "schema": SCHEMA,
        "data_policy": "三库均不存 JD / 简历 / OCR 原文或 PII；C 级数据零落盘",
        "init_scripts": ["deploy/sql/001_init.sql", "deploy/cypher/001_init.cypher"],
    }
