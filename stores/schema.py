#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""三库 Schema 单一来源：Python 运行时与 deploy/ 下的初始化脚本共用同一份定义。

重要：这些表只存**审计元数据与结构化事件**，不存 JD 原文、简历原文、
截图 OCR 原文或任何 PII。C 级数据仍是「原文零落盘」，见 privacy.py / user_jd.py。
"""

POSTGRES_DDL = """
-- 职引者 · PostgreSQL 初始化（幂等）
CREATE TABLE IF NOT EXISTS zhiyin_events (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    kind        TEXT NOT NULL,
    source_id   TEXT,
    grade       TEXT,
    detail      JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_zhiyin_events_ts    ON zhiyin_events (ts DESC);
CREATE INDEX IF NOT EXISTS idx_zhiyin_events_kind  ON zhiyin_events (kind);

CREATE TABLE IF NOT EXISTS zhiyin_tasks (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    state       TEXT NOT NULL,
    backend     TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    detail      JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS zhiyin_source_assessments (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    action      TEXT NOT NULL,
    source_id   TEXT NOT NULL,
    grade       TEXT,
    verdict     TEXT,
    detail      JSONB NOT NULL DEFAULT '{}'::jsonb
);
"""

SQLITE_DDL = """
-- 职引者 · SQLite 降级库（幂等），表结构与 PostgreSQL 逻辑等价
CREATE TABLE IF NOT EXISTS zhiyin_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    kind        TEXT NOT NULL,
    source_id   TEXT,
    grade       TEXT,
    detail      TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_zhiyin_events_ts   ON zhiyin_events (ts DESC);
CREATE INDEX IF NOT EXISTS idx_zhiyin_events_kind ON zhiyin_events (kind);

CREATE TABLE IF NOT EXISTS zhiyin_tasks (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    state       TEXT NOT NULL,
    backend     TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    finished_at TEXT,
    detail      TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS zhiyin_source_assessments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    action      TEXT NOT NULL,
    source_id   TEXT NOT NULL,
    grade       TEXT,
    verdict     TEXT,
    detail      TEXT NOT NULL DEFAULT '{}'
);
"""

NEO4J_CYPHER = """// 职引者 · Neo4j 约束与索引（幂等）
CREATE CONSTRAINT skill_name IF NOT EXISTS FOR (s:Skill) REQUIRE s.name IS UNIQUE;
CREATE CONSTRAINT role_name  IF NOT EXISTS FOR (r:Role)  REQUIRE r.name IS UNIQUE;
CREATE INDEX skill_level IF NOT EXISTS FOR (s:Skill) ON (s.level);
"""

# 图模型：(:Role)-[:REQUIRES {level, hits}]->(:Skill)，(:Skill)-[:RELATED]->(:Skill)
GRAPH_MODEL = {
    "nodes": ["Skill(name, level, hits)", "Role(name)"],
    "edges": ["(Role)-[:REQUIRES {level, hits}]->(Skill)", "(Skill)-[:RELATED]->(Skill)"],
    "data_grade": "C（用户自带 JD 的结构化技能字段，已脱敏；不存原文与证据短句）",
}

# Redis key 规范：只缓存非敏感的公开聚合结果与短 TTL 状态
REDIS_KEY_SPEC = {
    "zhiyin:market:{major}": "公开岗位聚合快照，TTL 900s，D 级，无 PII",
    "zhiyin:task:{id}": "Celery 任务状态，TTL 3600s",
    "zhiyin:ratelimit:{source_id}": "数据源限频令牌，TTL 由 Crawl-delay 决定",
    "forbidden": "禁止缓存 JD 原文 / 简历 / OCR 文本 / 任何 PII；C 级数据不得进 Redis",
}
