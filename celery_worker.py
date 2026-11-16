#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""职引者 ZHIYIN · Celery worker 入口（仅容器 / 已装 celery + Redis 时使用）

启动：
    celery -A celery_worker:app worker --loglevel=info --concurrency=2

与 celery_app.py 的分工：
    - celery_app.py 负责 API 侧的任务派发与 eager 降级（本机默认路径）；
    - 本模块暴露标准 Celery 实例，供容器里的独立 worker 进程消费队列。
两边注册的任务名完全一致（zhiyin.<name>），因此 BFF 派发的任务可被 worker 执行。

任务函数只处理 D 级公开样本或已脱敏的结构化字段，不接收 JD / 简历 / OCR 原文。
"""
from __future__ import annotations

from celery import Celery  # type: ignore

from secure_config import cfg
from tasks import TASK_REGISTRY

app = Celery("zhiyin", broker=cfg.get("REDIS_URL"), backend=cfg.get("REDIS_URL"))
app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_track_started=True,
    result_expires=3600,
    worker_prefetch_multiplier=1,
)

for _name, _fn in TASK_REGISTRY.items():
    app.task(name="zhiyin." + _name)(_fn)
