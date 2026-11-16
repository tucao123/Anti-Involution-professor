-- 职引者 ZHIYIN · PostgreSQL 初始化（幂等）
-- 与 stores/schema.py::POSTGRES_DDL 保持一致；容器首次启动由 docker-entrypoint-initdb.d 执行。
-- 注意：这些表只存审计元数据 / 任务状态 / 来源评估，绝不存 JD 原文、简历、OCR 文本或 PII。

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
