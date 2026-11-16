#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""职引者 ZHIYIN · Embedding 适配层（BGE → DashScope → 本地 hash 降级）

设计原则：
1. 统一接口 embed(texts, scope) -> {vectors, backend, dim, semantic, cloud_used, note}。
   上层（vector_store / orchestration）不关心后端，只按 dim 建 Qdrant 集合。
2. scope 是合规参数，不是性能参数：
   - scope="public"：D 级公开岗位样本 / 脱敏技能标签，允许走云端 embedding。
   - scope="user"  ：C 级用户自带 JD / 简历 / 截图 OCR，**永远只走本地后端**，
     除非 ALLOW_CLOUD_FOR_USER_DATA 显式为 true（默认 false，且需法务书面结论）。
3. hash 后端复用 rag_store._features（字符 2/3-gram + 技能本体 TF-IDF），
   用 hashing trick 投影成稠密向量并 L2 归一化。它**不是语义向量**，
   semantic=False 会在状态和检索结果里如实标注，绝不冒充 BGE。
4. 任何后端失败都降级，不抛到 HTTP 层；密钥不打印、不落盘、不进异常消息。
"""
from __future__ import annotations

import hashlib
import json
import math
import urllib.error
import urllib.request
from typing import Any

import rag_store
from secure_config import cfg, qwen_gate, secret

_BGE: Any = None
_BGE_TRIED = False
_DASHSCOPE_FAILED = False


def _features(text: str) -> dict[str, float]:
    """复用本地稀疏检索的特征口径，保证两条检索链路可比、可复现。"""
    try:
        return rag_store._features(text)
    except Exception:
        return {}


def _hash_vector(text: str, dim: int) -> list[float]:
    # 静默产出零向量会让检索结果全部退化为 0 分；类型不符时直接暴露问题
    if not isinstance(text, str):
        raise TypeError("hash embedding requires str, got %s" % type(text).__name__)
    vec = [0.0] * dim
    for feat, weight in _features(text).items():
        h = hashlib.blake2b(feat.encode("ascii", "ignore"), digest_size=8).digest()
        idx = int.from_bytes(h[:4], "big") % dim
        sign = 1.0 if h[4] & 1 else -1.0
        vec[idx] += sign * float(weight)
    norm = math.sqrt(sum(v * v for v in vec))
    return [v / norm for v in vec] if norm else vec


def _bge_package_installed() -> bool:
    import importlib.util
    return importlib.util.find_spec("sentence_transformers") is not None


def _bge_available() -> bool:
    """只在 BGE_AUTOLOAD=true 时才真正加载权重（可能触发数百 MB 下载）。

    这样 /api/health 与常规检索不会被模型下载阻塞；权重未就绪时 auto 依次落到
    DashScope（仅 public）或本地 hash，状态接口如实标注，不冒充语义向量。
    """
    global _BGE, _BGE_TRIED
    if _BGE_TRIED:
        return _BGE is not None
    if not cfg.flag("BGE_AUTOLOAD", False) or not _bge_package_installed():
        return False
    _BGE_TRIED = True
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
        _BGE = SentenceTransformer(cfg.get("BGE_MODEL"))
    except Exception:
        _BGE = None
        _BGE_TRIED = False  # 允许下载完成后再次尝试
    return _BGE is not None


def _bge_embed(texts: list[str]) -> list[list[float]]:
    arr = _BGE.encode(texts, normalize_embeddings=True, batch_size=16)
    return [[float(x) for x in row] for row in arr]


def _dashscope_allowed(scope: str) -> bool:
    if scope == "user" and not cfg.flag("ALLOW_CLOUD_FOR_USER_DATA"):
        return False
    if scope == "public" and not cfg.flag("ALLOW_PUBLIC_SAMPLE_TO_CLOUD", True):
        return False
    # 端点不可信（key 形态不符且未自定义 base_url）时不发起请求，直接降级，
    # 避免误把文本发给不匹配的网关，也避免健康检查被无谓超时拖住。
    return qwen_gate()["can_call_embedding"]


def _dashscope_embed(texts: list[str]) -> list[list[float]]:
    """DashScope OpenAI 兼容 /embeddings；纯标准库 HTTP，无需 SDK。"""
    global _DASHSCOPE_FAILED
    base = cfg.get("QWEN_BASE_URL").rstrip("/")
    key = secret("QWEN_API_KEY")
    if not key:
        raise RuntimeError("NO_KEY")
    body = json.dumps({"model": cfg.get("QWEN_EMBED_MODEL"), "input": texts},
                      ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        base + "/embeddings", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + key,
                 "Accept": "application/json"},
        method="POST",
    )
    # embedding 探测用短超时，避免健康检查 / 首次向量化被不可达端点拖住
    timeout = min(cfg.integer("QWEN_TIMEOUT", 30), 8)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8", errors="ignore"))
    rows = sorted(data.get("data", []), key=lambda d: d.get("index", 0))
    out = [[float(x) for x in r["embedding"]] for r in rows]
    if len(out) != len(texts):
        raise RuntimeError("BAD_SHAPE")
    return out


def embed(texts: list[str], scope: str = "public") -> dict[str, Any]:
    """统一入口。scope 必须是 public 或 user；user 默认禁止任何云端外发。"""
    global _DASHSCOPE_FAILED
    if scope not in ("public", "user"):
        scope = "user"  # 未知范围按最严格处理
    chosen = cfg.get("EMBED_BACKEND", "auto").lower()
    candidates = (["bge", "dashscope", "hash"] if chosen == "auto"
                  else [chosen, "hash"] if chosen != "hash" else ["hash"])
    last_note = ""
    for backend in candidates:
        try:
            if backend == "bge":
                if not _bge_available():
                    last_note = "BGE 本地模型未安装或加载失败"
                    continue
                vectors = _bge_embed(texts)
                return _pack(vectors, "bge", True, False,
                             "BGE 本地语义向量；用户数据全程不出本机")
            if backend == "dashscope":
                if not _dashscope_allowed(scope) or _DASHSCOPE_FAILED:
                    last_note = "DashScope embedding 未获授权或此前调用失败"
                    continue
                vectors = _dashscope_embed(texts)
                return _pack(vectors, "dashscope-text-embedding-v3", True, True,
                             "云端语义向量；仅用于 D 级公开样本或已显式授权的数据")
            if backend == "hash":
                dim = cfg.integer("EMBED_DIM", 512)
                vectors = [_hash_vector(t, dim) for t in texts]
                return _pack(vectors, "local-hash-ngram", False, False,
                             "确定性字符 n-gram 投影（非语义向量）；与本地稀疏检索同口径")
        except urllib.error.HTTPError as exc:
            if backend == "dashscope":
                _DASHSCOPE_FAILED = True
            last_note = "后端 %s 返回 HTTP %d" % (backend, exc.code)
        except Exception as exc:  # 不回显上游细节，避免泄露请求头 / 凭据
            if backend == "dashscope":
                _DASHSCOPE_FAILED = True
            last_note = "后端 %s 不可用（%s）" % (backend, type(exc).__name__)
    # 理论不可达（hash 必然成功），保留兜底以满足 fail-closed
    dim = cfg.integer("EMBED_DIM", 512)
    return _pack([_hash_vector(t, dim) for t in texts], "local-hash-ngram",
                 False, False, last_note or "全部后端降级")


def _pack(vectors: list[list[float]], backend: str, semantic: bool,
          cloud_used: bool, note: str) -> dict[str, Any]:
    return {
        "ok": True,
        "vectors": vectors,
        "backend": backend,
        "dim": len(vectors[0]) if vectors else cfg.integer("EMBED_DIM", 512),
        "semantic": semantic,
        "cloud_used": cloud_used,
        "note": note,
    }


def status() -> dict[str, Any]:
    """可观测状态：纯推导，不触发任何网络调用（健康检查必须快）。"""
    from secure_config import public_view
    bge_ok = _bge_available()
    key_ok = bool(secret("QWEN_API_KEY"))
    cloud_ok = (key_ok and cfg.flag("ALLOW_PUBLIC_SAMPLE_TO_CLOUD", True)
                and qwen_gate()["can_call_embedding"] and not _DASHSCOPE_FAILED)
    chosen = cfg.get("EMBED_BACKEND", "auto").lower()
    HASH, DASHSCOPE = "local-hash-ngram", "dashscope-text-embedding-v3"

    def pick(scope: str) -> str:
        allow_cloud = cloud_ok and (scope == "public" or
                                    cfg.flag("ALLOW_CLOUD_FOR_USER_DATA"))
        if chosen == "hash":
            return HASH
        if chosen == "bge":
            return "bge" if bge_ok else HASH
        if chosen == "dashscope":
            return DASHSCOPE if allow_cloud else HASH
        return "bge" if bge_ok else (DASHSCOPE if allow_cloud else HASH)

    return {
        "ok": True,
        "configured_backend": chosen,
        "bge_model": cfg.get("BGE_MODEL"),
        "bge_available": bge_ok,
        "bge_package_installed": _bge_package_installed(),
        "bge_autoload": cfg.flag("BGE_AUTOLOAD", False),
        "bge_reason": ("已加载本地权重" if bge_ok else
                       "BGE_AUTOLOAD=false，未加载权重（避免健康检查触发下载）"
                       if not cfg.flag("BGE_AUTOLOAD", False) else
                       "sentence-transformers 未安装" if not _bge_package_installed() else
                       "本地权重或依赖加载失败，已自动降级（不冒充语义向量）"),
        "dashscope_configured": key_ok,
        "dashscope_key_verified": public_view()["llm_key_verified"],
        "hash_dim": cfg.integer("EMBED_DIM", 512),
        "selected_backend": {"public": pick("public"), "user": pick("user")},
        "semantic": {"bge": True, DASHSCOPE: True, HASH: False},
        "user_data_stays_local": not cfg.flag("ALLOW_CLOUD_FOR_USER_DATA"),
        "note": "hash 后端是确定性字符 n-gram 投影，不是语义向量；状态如实标注，不冒充 BGE",
    }
