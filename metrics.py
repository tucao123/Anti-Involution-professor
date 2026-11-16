#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""职引者 ZHIYIN · Prometheus 指标与 SLO 视图（纯标准库，零新增依赖）

/metrics   Prometheus text exposition（0.0.4）
/api/slo   SLO 定义、错误预算与当前进程窗口内的实测值

设计约束：
1. 指标标签必须低基数：路径先归一化，绝不放用户 ID、查询串、凭据或文本。
2. 不记录请求体 / 响应体 / 请求头；只记录方法、归一化路径、状态码与耗时。
3. 进程内计数，重启清零；长期 SLO 由 Prometheus 侧 30d 窗口计算，
   本模块只提供单实例即时值与目标定义。
"""
from __future__ import annotations

import re
import threading
import time
from collections import defaultdict
from typing import Any

from secure_config import cfg

_LOCK = threading.Lock()
_START = time.time()
_VERSION = "2.0.0"
_BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

_COUNTERS: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
_HIST: dict[tuple[str, tuple[tuple[str, str], ...]], dict[str, float]] = defaultdict(
    lambda: {"count": 0.0, "sum": 0.0, **{("b" + str(b)): 0.0 for b in _BUCKETS}}
)

_PATH_ID = re.compile(r"/[0-9a-f]{8,}|/[0-9a-f-]{36}")

SLO = {
    "service": "zhiyin-bff",
    "version": _VERSION,
    "window_days": cfg.integer("SLO_WINDOW_DAYS", 30),
    "objectives": [
        {"name": "availability", "target": 0.995, "unit": "ratio",
         "sli": "非 5xx 响应 / 全部响应", "error_budget": "30d 内约 216 分钟不可用"},
        {"name": "latency_p95", "target_ms": 800, "unit": "ms",
         "sli": "HTTP 请求耗时 P95（/api/*）", "error_budget": "P95 > 800ms 视为预算消耗"},
        {"name": "schema_validation_success", "target": 0.99, "unit": "ratio",
         "sli": "模型输出通过 Pydantic JSON Schema 校验的比例", "error_budget": "失败即降级，不渲染未校验内容"},
        {"name": "compliance_gate_integrity", "target": 1.0, "unit": "ratio",
         "sli": "C 级数据外发 / 未批准来源绕过的次数为 0", "error_budget": "零容忍，无错误预算"},
    ],
    "note": "SLO 是工程目标，不是法律意见；合规闸门目标恒为 1.0，不允许用错误预算换放宽。",
}


def _norm_path(path: str) -> str:
    path = (path or "/").split("?")[0]
    path = _PATH_ID.sub("/:id", path)
    return path[:120]


def _labels(pairs: tuple[tuple[str, str], ...]) -> str:
    if not pairs:
        return ""
    items = []
    for k, v in pairs:
        val = str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        items.append('%s="%s"' % (k, val))
    return "{" + ",".join(items) + "}"


def inc(name: str, value: float = 1.0, **labels: Any) -> None:
    key = (name, tuple(sorted((str(k), str(v)) for k, v in labels.items())))
    with _LOCK:
        _COUNTERS[key] += value


def observe(method: str, path: str, status: int, duration: float) -> None:
    p = _norm_path(path)
    inc("zhiyin_http_requests_total", method=method, path=p, status=str(status))
    key = ("zhiyin_http_request_duration_seconds", (("path", p),))
    with _LOCK:
        h = _HIST[key]
        h["count"] += 1
        h["sum"] += max(float(duration), 0.0)
        for b in _BUCKETS:
            if duration <= b:
                h["b" + str(b)] += 1
    if status >= 500:
        inc("zhiyin_http_5xx_total", path=p)
    if status in (400, 403):
        inc("zhiyin_gate_rejections_total", path=p, code=str(status))


def _p95(path: str | None = None) -> float:
    with _LOCK:
        items = [(k, dict(v)) for k, v in _HIST.items() if path is None or dict(k[1]).get("path") == path]
    total = sum(v["count"] for _, v in items)
    if not total:
        return 0.0
    target = 0.95 * total
    cumulative = 0.0
    for b in _BUCKETS:
        cumulative += sum(v.get("b" + str(b), 0) for _, v in items)
        if cumulative >= target:
            return b * 1000.0
    return _BUCKETS[-1] * 1000.0


def render() -> str:
    lines: list[str] = [
        "# HELP zhiyin_build_info 职引者构建信息",
        "# TYPE zhiyin_build_info gauge",
        'zhiyin_build_info{version="%s",phase="2"} 1' % _VERSION,
        "",
        "# HELP zhiyin_process_uptime_seconds 进程运行时长",
        "# TYPE zhiyin_process_uptime_seconds gauge",
        "zhiyin_process_uptime_seconds %.2f" % (time.time() - _START),
        "",
    ]
    with _LOCK:
        counters = dict(_COUNTERS)
        hist = {k: dict(v) for k, v in _HIST.items()}
    names = sorted({k[0] for k in counters})
    for name in names:
        lines.append("# TYPE %s counter" % name)
        for (n, pairs), value in sorted(counters.items(), key=lambda kv: str(kv[0])):
            if n == name:
                lines.append("%s%s %.6g" % (n, _labels(pairs), value))
        lines.append("")
    for (name, pairs), h in sorted(hist.items(), key=lambda kv: str(kv[0])):
        lines.append("# TYPE %s histogram" % name)
        for b in _BUCKETS:
            lines.append('%s_bucket%s %.6g' % (
                name, _labels(pairs + (("le", str(b)),)), h.get("b" + str(b), 0)))
        lines.append('%s_bucket%s %.6g' % (name, _labels(pairs + (("le", "+Inf"),)), h["count"]))
        lines.append("%s_sum%s %.6g" % (name, _labels(pairs), h["sum"]))
        lines.append("%s_count%s %.6g" % (name, _labels(pairs), h["count"]))
        lines.append("")

    # 组件健康 gauge（1 = 真服务已连接，0 = 降级）
    try:
        import stores
        st = stores.status()
        lines.append("# HELP zhiyin_component_up 组件是否连到真服务（0=本地降级）")
        lines.append("# TYPE zhiyin_component_up gauge")
        for comp, key in (("postgresql", "postgresql"), ("redis", "redis"), ("neo4j", "neo4j")):
            lines.append('zhiyin_component_up{component="%s"} %d' %
                         (comp, 1 if st[key]["connected"] and
                          not str(st[key]["backend"]).endswith("fallback") else 0))
        lines.append("")
    except Exception:
        pass
    return "\n".join(lines) + "\n"


def slo_view() -> dict[str, Any]:
    with _LOCK:
        counters = dict(_COUNTERS)
    total = sum(v for (n, _), v in counters.items() if n == "zhiyin_http_requests_total")
    bad = sum(v for (n, _), v in counters.items() if n == "zhiyin_http_5xx_total")
    availability = 1.0 - (bad / total) if total else 1.0
    p95 = _p95()
    return {
        "ok": True,
        "slo": SLO,
        "current_instance_window": {
            "since_process_start": True,
            "requests_total": int(total),
            "errors_5xx": int(bad),
            "availability": round(availability, 6),
            "latency_p95_ms_estimate": p95,
            "note": "进程内即时值，重启清零；正式 SLO 由 Prometheus 30d 窗口计算",
        },
        "alerts": "deploy/alerts.yml",
        "prometheus": "deploy/prometheus.yml",
        "metrics_endpoint": "/metrics",
    }
