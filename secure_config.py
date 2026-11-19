#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""职引者 ZHIYIN · 统一安全配置层（Phase 3）

安全约定（与 server.py Phase 1 一致，且更严格）：
1. 凭据只存在于本进程内存：不写入任何项目文件、不打印到日志、不返回给浏览器、
   不回显到错误响应。public_view() 只返回「是否配置 / 来源类别 / 形态」，绝不返回值。
2. 只加载本项目确实需要的键；key.env 里其他 provider（openai / bigmodel / pixso /
   路径行 / mysql 等）一律跳过，不解析、不载入内存。
3. 支持两类配置格式：
   - 标准 KEY=value 或 KEY: value
   - 笔记式「标签行 + 续行值」：如 `qwen:` 下一行才是 key，
     `qdrant：` 下一行是 API key，`Cluster Endpoint：` 下一行是 URL。
4. 优先级：进程环境变量 > --env-file / ZHIYIN_ENV_FILE > 项目 .env >
   .zhiyin_key_path 指向的外部文件。指针文件只含一行路径、不含密钥、不入库。
5. 云端组件默认收窄：ALLOW_CLOUD_FOR_USER_DATA=false（C 级用户自带数据不上云，长期不变）；
   LLM_ENABLED 由 .env 决定（本机已置 true）。端点可信性按「目标域是否官方 DashScope 或
   显式自定义中转」判定，不再用 key 字符串形态一刀切否决：实测非 sk-<32hex> 形态的 key
   在官方兼容端点同样返回 200。启用后仍只有结构化技能字段可外发，C 级原文前置拦截。

用法：
    from secure_config import cfg, secret, public_view
    cfg.get("QWEN_MODEL")            # 非敏感配置
    secret("QDRANT_API_KEY")         # 敏感值，仅服务端内部调用
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
POINTER = ROOT / ".zhiyin_key_path"

# 非敏感默认值。敏感项一律不在此处给默认值。
DEFAULTS: dict[str, str] = {
    # ---- 语言模型（qwen） ----
    "LLM_PROVIDER": "qwen",
    "LLM_ENABLED": "false",
    "QWEN_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "QWEN_MODEL": "qwen-plus",
    "QWEN_TIMEOUT": "30",
    # ---- 本地多模态模型（Ollama / Qwen3-VL，图片不出机） ----
    "LOCAL_VLM_BASE_URL": "http://127.0.0.1:11434",
    "LOCAL_VLM_API_STYLE": "ollama",   # ollama 原生 /api/chat（默认，think 控制可靠）| openai /v1/chat/completions
    "LOCAL_VLM_MODEL": "qwen3-vl:4b-instruct",
    "LOCAL_VLM_TIMEOUT": "120",
    "LOCAL_VLM_MAX_TOKENS": "4096",
    # 模型目录留空：优先用 OLLAMA_MODELS 环境变量，否则走 Ollama 默认目录。
    # 仅影响 status() 里"模型目录"的展示，不影响解析调用（解析走 Ollama HTTP API）。
    # 本机若放在自定义目录，请在 .env 配 LOCAL_VLM_MODELS_DIR 或设 OLLAMA_MODELS。
    "LOCAL_VLM_MODELS_DIR": "",
    # ---- 向量库（Qdrant Cloud） ----
    "QDRANT_COLLECTION": "zhiyin_public_d_bge",
    "QDRANT_TIMEOUT": "8",
    "QDRANT_ALLOW_WRITE": "false",
    # ---- Embedding（服务端固定本地 BGE，hash 兜底；不向用户开放选择） ----
    "EMBED_BACKEND": "bge",           # 服务端固定：bge（本地）→ hash（确定性投影）
    "BGE_MODEL": "BAAI/bge-small-zh-v1.5",
    "BGE_DIM": "512",
    # 服务器固定本地 embedding：权重已随镜像 / .bge_env 预置时自动加载。
    # 仅离线冒烟测试可改为 false（将降级为本地 hash 投影，不冒充语义向量）。
    "BGE_AUTOLOAD": "true",
    "EMBED_DIM": "512",
    # ---- 三库 ----
    "POSTGRES_HOST": "127.0.0.1",
    "POSTGRES_PORT": "5432",
    "POSTGRES_DB": "zhiyin",
    "POSTGRES_USER": "zhiyin",
    "POSTGRES_SSLMODE": "disable",
    "REDIS_URL": "redis://127.0.0.1:6379/0",
    "NEO4J_URI": "bolt://127.0.0.1:7687",
    "NEO4J_USER": "neo4j",
    # ---- Celery ----
    "CELERY_TASK_ALWAYS_EAGER": "false",
    "CELERY_CONCURRENCY": "2",
    # ---- 合规开关 ----
    "ALLOW_CLOUD_FOR_USER_DATA": "false",
    "ALLOW_PUBLIC_SAMPLE_TO_CLOUD": "true",   # D 级公开岗位样本（无 PII）可上云
    "METRICS_ENABLED": "true",
    "SLO_WINDOW_DAYS": "30",
    # ---- 认证 / 分角色（研究学习原型） ----
    # off：不校验；optional：有令牌就验身份与角色、无令牌放行（本地研究/回归默认）；
    # required：所有受保护写端点必须带有效令牌且角色匹配。正式部署应设 required。
    "AUTH_MODE": "optional",
    "AUTH_SESSION_TTL": "86400",
    "AUTH_DEMO_ENABLED": "true",   # 未配置真实微信/GitHub 凭据时开放标注清楚的演示快捷登录
    "AUTH_REQUIRE_LOGIN": "true",  # 页面级强制登录：未登录访问工作台自动跳转登录页
    "AUTH_LOCAL_ACCOUNTS": "true", # 本地账号注册/登录/改密（用户名或邮箱 + 密码）
}

SECRET_KEYS = frozenset({
    "QWEN_API_KEY", "QDRANT_API_KEY", "QDRANT_URL", "POSTGRES_PASSWORD",
    "NEO4J_PASSWORD", "DEEPSEEK_API_KEY", "REDIS_URL",
    # 认证密钥：微信应用密钥 / 会话签名密钥 / 管理员密码哈希，一律不回显浏览器。
    "WECHAT_APPID", "WECHAT_APPSECRET", "WECHAT_REDIRECT_URI",
    "GITHUB_CLIENT_ID", "GITHUB_SECRET", "GITHUB_REDIRECT_URI",
    "AUTH_SECRET", "ADMIN_PASSWORD_HASH", "ZHIYIN_PII_KEY",
})

# 笔记式标签 → 内部键名。只登记本项目会用到的，其余 provider 一律跳过。
LABEL_MAP: dict[str, str] = {
    "deepseek": "DEEPSEEK_API_KEY",
    "qwen": "QWEN_API_KEY",
    "qdrant": "QDRANT_API_KEY",
    "cluster endpoint": "QDRANT_URL",
    "qdrant url": "QDRANT_URL",
    "postgresqlpassword": "POSTGRES_PASSWORD",
    "postgrespassword": "POSTGRES_PASSWORD",
    "neoj4password": "NEO4J_PASSWORD",
    "neo4jpassword": "NEO4J_PASSWORD",
}


def _candidate_paths(extra: Path | None = None):
    """按优先级生成 (来源类别, 路径)。来源类别可观测，路径本身绝不外泄。"""
    if extra:
        yield "explicit", Path(extra).expanduser()
    custom = os.environ.get("ZHIYIN_ENV_FILE")
    if custom:
        yield "env-file", Path(custom).expanduser()
    yield "project-dotenv", ROOT / ".env"
    if os.environ.get("ZHIYIN_DISABLE_LEGACY_KEY") != "1" and POINTER.is_file():
        try:
            target = POINTER.read_text(encoding="utf-8").strip()
        except OSError:
            target = ""
        if target and not target.startswith("#"):
            yield "external-key-path", Path(target).expanduser()


def _clean(value: str) -> str:
    return value.strip().strip('"').strip("'").strip()


def _parse(text: str) -> dict[str, str]:
    """解析标准行 + 笔记式「标签 + 续行」。未知 provider 的内容不进入结果。"""
    out: dict[str, str] = {}
    pending: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        # 1) 独立 URL 行优先消费 pending（否则 `https://host` 会被误判成标签 `https`）
        if re.match(r"^(?:https?|redis|rediss|bolt|neo4j)://", line, re.I):
            if pending:
                out[pending] = _clean(line)
                pending = None
            continue

        # 2) 标准 KEY=value（只接受白名单键，避免把任意文本当配置）
        m = re.match(r"^([A-Z][A-Z0-9_]{2,})\s*=\s*(.*)$", line)
        if m and ((m.group(1) in DEFAULTS) or (m.group(1) in SECRET_KEYS)):
            key, val = m.group(1), _clean(m.group(2))
            if val:
                out[key] = val
            pending = None
            continue

        # 3) 标签行（含中文冒号），可能有同行值
        m = re.match(r"^([A-Za-z][A-Za-z0-9_ \-]{1,40}?)\s*[:：]\s*(.*)$", line)
        if m:
            label, val = m.group(1).strip().lower(), _clean(m.group(2))
            mapped = LABEL_MAP.get(label)
            if val:
                if mapped:
                    out[mapped] = val
                pending = None
            else:
                pending = mapped  # 等下一行取值；未知标签为 None，其续行会被跳过
            continue

        # 4) 续行值：只有上一个是已登记标签才消费
        if pending:
            val = _clean(line)
            if val and not val.startswith("#"):
                out[pending] = val
            pending = None
    return out


class Config:
    def __init__(self, extra: Path | None = None) -> None:
        self._values: dict[str, str] = {}
        self._sources: dict[str, str] = {}
        self.file_sources: list[str] = []
        for source, path in _candidate_paths(extra):
            if not path or not path.is_file():
                continue
            try:
                parsed = _parse(path.read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                continue
            # 不 break：允许「.env 覆盖非敏感项 + 外部 key.env 提供密钥」共存，
            # 仍按候选顺序先到先得（高优先级文件的同名键不会被低优先级覆盖）。
            for k, v in parsed.items():
                if k not in self._values:
                    self._values[k] = v
                    self._sources[k] = source
            if parsed and source not in self.file_sources:
                self.file_sources.append(source)
        self.file_source = self.file_sources[0] if self.file_sources else "none"
        # 进程环境变量优先级最高（容器 / CI 注入用）
        for k in list(DEFAULTS) + list(SECRET_KEYS):
            if k in os.environ and os.environ[k].strip():
                self._values[k] = os.environ[k].strip()
                self._sources[k] = "process-env"

    def get(self, key: str, default: str | None = None) -> str:
        if key in self._values:
            return self._values[key]
        return DEFAULTS.get(key, default or "")

    def flag(self, key: str, default: bool = False) -> bool:
        v = self.get(key, "true" if default else "false").strip().lower()
        return v in ("1", "true", "yes", "on")

    def integer(self, key: str, default: int) -> int:
        try:
            return int(self.get(key, str(default)))
        except ValueError:
            return default

    def has(self, key: str) -> bool:
        return bool(self._values.get(key))

    def source(self, key: str) -> str:
        return self._sources.get(key, "default" if key in DEFAULTS else "none")

    def reload(self, extra: Path | None = None) -> None:
        """原地重载（--env-file 用）。原地更新可让所有 `from secure_config import cfg`
        的引用同步生效，避免各模块持有不同的配置快照。"""
        fresh = Config(extra)
        self._values.clear()
        self._values.update(fresh._values)
        self._sources.clear()
        self._sources.update(fresh._sources)
        self.file_sources[:] = fresh.file_sources
        self.file_source = fresh.file_source


cfg = Config()


def secret(key: str) -> str | None:
    """仅供服务端内部调用；调用方不得把返回值写入日志、响应或文件。"""
    if key not in SECRET_KEYS:
        return None
    return cfg._values.get(key) or None


DEFAULT_QWEN_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"


_LAST_QWEN_OK: bool | None = None


def note_qwen_call(ok: bool) -> None:
    """由语言模型层在真实调用后登记结果，仅进程内存，不含任何请求内容。"""
    global _LAST_QWEN_OK
    _LAST_QWEN_OK = bool(ok)


def qwen_gate() -> dict[str, Any]:
    """qwen 调用前的统一判定：端点由配置决定，可用性由真实响应决定。

    修订（2026-11-15）：此前因 key 形态不是 sk-<32hex> 就判定端点不可信，
    实测该 key 在官方 DashScope 兼容端点的 chat / embeddings 均返回 200，
    属于误判。现在只有两种情况拒绝：
      - 未配置 key；
      - QWEN_BASE_URL 既不是官方 DashScope 域，也不是用户显式自定义的中转端点
        （避免把数据发往未确认的网关）。
    key 是否有效以真实 HTTP 结果为准，失败由 llm.py 自动降级，不影响服务。
    chat 还需 LLM_ENABLED=true；embedding 只需端点可信 + scope 授权。
    """
    key = secret("QWEN_API_KEY") or ""
    base = cfg.get("QWEN_BASE_URL").rstrip("/")
    official_shape = bool(re.match(r"^sk-[0-9a-fA-F]{32}$", key))
    custom_base = base != DEFAULT_QWEN_BASE.rstrip("/")
    host = base.split("//", 1)[-1].split("/", 1)[0].lower()
    official_host = host == "dashscope.aliyuncs.com" or host.endswith(".dashscope.aliyuncs.com")
    # 端点可信性只由「配置里显式给出的目标」决定：官方 DashScope 域或用户自定义中转。
    # key 是否真的可用以真实 HTTP 响应为准（llm.py / embedding.py 失败即自动降级），
    # 不再用 key 的字符串形态一刀切否决：实测非 sk-<32hex> 形态在官方兼容端点返回 200。
    endpoint_ok = bool(key) and (official_host or custom_base or official_shape)
    enabled = cfg.flag("LLM_ENABLED")
    if not key:
        reason = "QWEN_API_KEY 未配置，请在 .env 中填写；服务已自动降级到离线规则引擎"
    elif not endpoint_ok:
        reason = ("QWEN_BASE_URL 既不是官方 DashScope 域也不是显式自定义兼容端点；"
                  "请核对端点后再启用，避免把数据发往未确认的网关")
    elif not enabled:
        reason = "LLM_ENABLED=false（默认关闭）；确认数据边界后置为 true 才会外发"
    else:
        reason = "qwen 端点已就绪"
    return {
        "key_present": bool(key),
        "key_official_shape": official_shape,
        "custom_base": custom_base,
        "official_host": official_host,
        "endpoint_trustworthy": endpoint_ok,
        "last_call_ok": _LAST_QWEN_OK,
        "llm_enabled": enabled,
        "can_call_chat": endpoint_ok and enabled,
        "can_call_embedding": endpoint_ok,
        "model": cfg.get("QWEN_MODEL"),
        "reason": reason,
    }


def _shape(key: str, value: str) -> str:
    if not value:
        return "empty"
    if value.startswith(("http://", "https://", "redis://", "rediss://", "bolt://")):
        # 基础设施端点（尤其云实例 UUID 主机名）不下发浏览器，只回协议类别
        return "url:" + value.split("://", 1)[0] + "(host-redacted)"
    if key.endswith("_KEY"):
        # DashScope 官方 key 形如 sk-<32 hex>；其他形态标注为待确认
        if re.match(r"^sk-[0-9a-fA-F]{32}$", value):
            return "dashscope-key"
        if re.match(r"^sk-[A-Za-z0-9_.\-]{8,}$", value):
            return "nonstandard-key(unverified)"
        if value.count(".") >= 2:
            return "jwt-like-key"
        return "opaque-key"
    return "set"


def public_view() -> dict[str, Any]:
    """可经 HTTP 返回的状态视图：只有是否配置、来源类别与形态，绝不含值。"""
    keys = sorted(set(DEFAULTS) | SECRET_KEYS)
    view: dict[str, Any] = {}
    for k in keys:
        raw = cfg._values.get(k, "")
        entry: dict[str, Any] = {
            "configured": bool(raw),
            "source": cfg.source(k),
            "sensitive": k in SECRET_KEYS,
        }
        if k in SECRET_KEYS:
            entry["shape"] = _shape(k, raw) if raw else "empty"
        elif raw:
            entry["value"] = raw
        else:
            entry["value"] = DEFAULTS.get(k, "")
        view[k] = entry
    return {
        "ok": True,
        "file_source": cfg.file_source,
        "file_sources": cfg.file_sources,
        "sensitive_values_never_returned": True,
        "keys": view,
        "cloud_user_data_allowed": cfg.flag("ALLOW_CLOUD_FOR_USER_DATA"),
        "llm_enabled": cfg.flag("LLM_ENABLED"),
        "llm_key_verified": _shape("QWEN_API_KEY", secret("QWEN_API_KEY") or "") == "dashscope-key",
    }
