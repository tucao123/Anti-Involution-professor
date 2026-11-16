#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""图库适配器：Neo4j 优先，缺失时降级进程内邻接表（零新增依赖）。

图模型：(:Role)-[:REQUIRES {level, hits}]->(:Skill)。
只写「技能名 / 级别 / 命中次数」等已脱敏的结构化字段，
**不写 JD 原文、证据短句、简历或任何 PII**；本地降级数据不落盘、重启即清空。
"""
from __future__ import annotations

import threading
from typing import Any

from secure_config import cfg, secret

from .schema import GRAPH_MODEL, NEO4J_CYPHER

_LOCK = threading.Lock()
_DRIVER: Any = None
_BACKEND = "uninitialized"
_REASON = ""
_TRIED = False
_SKILLS: dict[str, dict[str, Any]] = {}
_EDGES: dict[str, dict[str, dict[str, Any]]] = {}

UPSERT_CYPHER = """
MERGE (r:Role {name: $role})
WITH r
UNWIND $skills AS s
MERGE (k:Skill {name: s.name})
SET k.level = s.level, k.hits = s.hits
MERGE (r)-[rel:REQUIRES]->(k)
SET rel.level = s.level, rel.hits = s.hits
"""

CONSTRAINTS = [line.strip() for line in NEO4J_CYPHER.splitlines()
               if line.strip() and not line.strip().startswith("//")]


def _connect() -> Any:
    global _DRIVER, _BACKEND, _REASON, _TRIED
    if _TRIED:
        return _DRIVER
    _TRIED = True
    try:
        from neo4j import GraphDatabase  # type: ignore
        pwd = secret("NEO4J_PASSWORD")
        if not pwd:
            raise RuntimeError("NO_NEO4J_PASSWORD")
        _DRIVER = GraphDatabase.driver(
            cfg.get("NEO4J_URI"), auth=(cfg.get("NEO4J_USER"), pwd),
            connection_timeout=3)
        _DRIVER.verify_connectivity()
        with _DRIVER.session() as session:
            for stmt in CONSTRAINTS:
                session.run(stmt)
        _BACKEND = "neo4j"
    except Exception as exc:
        _DRIVER = None
        _BACKEND = "local-adjacency"
        _REASON = "Neo4j 不可用（%s）" % type(exc).__name__
    return _DRIVER


def upsert_role_skills(role: str, skills: list[dict[str, Any]]) -> dict[str, Any]:
    """role 是岗位名（来自 JD 标题，已脱敏）；skills 只取 name/level/hits 字段。"""
    global _REASON
    clean = []
    for s in skills[:40]:
        name = str(s.get("name", "")).strip()[:48]
        if not name:
            continue
        clean.append({"name": name, "level": str(s.get("level", "implicit"))[:16],
                      "hits": int(s.get("hits", 0) or 0)})
    role = (role or "未命名岗位").strip()[:64]
    driver = _connect()
    if driver is not None and clean:
        try:
            with driver.session() as session:
                session.run(UPSERT_CYPHER, role=role, skills=clean)
            return {"ok": True, "backend": "neo4j", "role": role, "skills": len(clean)}
        except Exception as exc:
            _REASON = "Neo4j 写入失败（%s），已降级本地邻接表" % type(exc).__name__
    with _LOCK:
        for s in clean:
            _SKILLS[s["name"]] = {"level": s["level"], "hits": s["hits"]}
        _EDGES.setdefault(role, {})
        for s in clean:
            _EDGES[role][s["name"]] = {"level": s["level"], "hits": s["hits"]}
    return {"ok": True, "backend": _BACKEND, "role": role, "skills": len(clean)}


def neighbors(skill: str) -> list[dict[str, Any]]:
    """与某技能共同出现在同一岗位下的其他技能（共现，不是语义相似）。"""
    with _LOCK:
        out: dict[str, int] = {}
        for required in _EDGES.values():
            if skill in required:
                for other in required:
                    if other != skill:
                        out[other] = out.get(other, 0) + 1
    return [{"name": k, "cooccurrence": v} for k, v in
            sorted(out.items(), key=lambda kv: -kv[1])[:10]]


def overview() -> dict[str, Any]:
    with _LOCK:
        roles = list(_EDGES)[:10]
        edges = sum(len(v) for v in _EDGES.values())
        skills = [{"name": k, **v} for k, v in list(_SKILLS.items())[:20]]
    return {"roles": roles, "skill_count": len(skills), "edge_count": edges,
            "skills_sample": skills, "grade": "C（结构化脱敏字段）"}


def clear() -> int:
    with _LOCK:
        n = len(_EDGES)
        _EDGES.clear()
        _SKILLS.clear()
    return n


def status() -> dict[str, Any]:
    driver = _connect()
    with _LOCK:
        nodes, edges = len(_SKILLS), sum(len(v) for v in _EDGES.values())
    return {
        "backend": _BACKEND,
        "connected": driver is not None,
        "uri_configured": bool(cfg.get("NEO4J_URI")),
        "password_configured": bool(secret("NEO4J_PASSWORD")),
        "fallback_reason": _REASON,
        "local_nodes": nodes,
        "local_edges": edges,
        "persistent": _BACKEND == "neo4j",
        "model": GRAPH_MODEL,
        "cypher_init": "deploy/cypher/001_init.cypher",
        "data_policy": "只写技能名 / 级别 / 命中次数；不写原文、证据短句或 PII",
    }
