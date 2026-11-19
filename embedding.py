#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""职引者 ZHIYIN · Embedding 适配层（服务端固定本地 BGE → 本地 hash 降级）

设计原则：
1. 统一接口 embed(texts, scope) -> {vectors, backend, dim, semantic, cloud_used, note}。
   上层（vector_store / orchestration）不关心后端，只按 dim 建 Qdrant 集合。
2. Embedding 是**服务器侧固定能力**，不向用户开放选择：
   - 语义向量统一走本地 BGE（BAAI/bge-small-zh-v1.5，512 维），不依赖云端 embedding-v3；
   - 本地 BGE 不可用时降级为确定性 hash 投影，并如实标注 semantic=False；
   - 用户可在个人中心选择「语言模型 API」，但**无法选择 / 覆盖 embedding 模型**。
3. scope 仍是合规参数：scope="user"（C 级用户自带 JD / 简历 / OCR）永远只走本地后端，
   与固定本地 embedding 策略一致；scope="public" 同样不再调用云端 embedding。
4. hash 后端复用 rag_store._features（字符 2/3-gram + 技能本体 TF-IDF），
   用 hashing trick 投影成稠密向量并 L2 归一化。它**不是语义向量**，
   semantic=False 会在状态和检索结果里如实标注，绝不冒充 BGE。
5. 任何后端失败都降级，不抛到 HTTP 层；不产生任何 embedding 云端请求。
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# BGE 独立依赖目录（2026-11-16 恢复）
#   主环境 site-packages 中 transformers 4.57.6 与 sentence-transformers 6.3.0
#   版本互斥（后者要求 transformers>=5,<6），且目录混入两套文件，触发
#   `_LazyModule` 循环导入。反复覆盖安装只会让主环境更不一致，因此不再修补它，
#   改用 pip --target 生成的干净组合（transformers 4.49.0 + ST 3.4.1 +
#   tokenizers 0.21.0 + hf-hub 0.29.3 + Pillow 11.1.0）并前置到 sys.path。
#   目录为 gitignored 本地产物；不存在时静默沿用主环境，降级路径完全不变。
# ---------------------------------------------------------------------------
import os
import sys
from pathlib import Path as _Path

_BGE_ENV = _Path(__file__).resolve().parent / ".bge_env"
if _BGE_ENV.is_dir() and str(_BGE_ENV) not in sys.path:
    sys.path.insert(0, str(_BGE_ENV))

import hashlib
import math
from typing import Any

import rag_store
from secure_config import cfg

_BGE: Any = None
_BGE_TRIED = False


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


def _bge_model_path() -> str:
    """优先把 HF repo id 解析为本地 hub snapshot 绝对路径。

    huggingface-hub 的 `.no_exist` 缓存标记会让 SentenceTransformer 误判
    `modules.json` 不存在，从而打印「Creating a new one with mean pooling」并用
    mean pooling 重建，丢掉 BGE 官方的 CLS pooling + Normalize 配置。直接指向
    完整 snapshot 目录即可按官方 modules.json 加载；找不到时返回原 repo id。
    """
    name = str(cfg.get("BGE_MODEL") or "")
    if not name or "/" not in name or _Path(name).is_dir():
        return name
    org, repo = name.split("/", 1)
    hub = _Path(os.environ.get("HF_HOME", str(_Path.home() / ".cache" / "huggingface")))
    base = hub / "hub" / ("models--%s--%s" % (org, repo)) / "snapshots"
    if not base.is_dir():
        return name
    for d in sorted(base.iterdir()):
        if (d.is_dir() and (d / "modules.json").is_file()
                and ((d / "model.safetensors").is_file()
                     or (d / "pytorch_model.bin").is_file())):
            return str(d)
    return name


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
        _BGE = SentenceTransformer(_bge_model_path())
    except Exception:
        _BGE = None
        _BGE_TRIED = False  # 允许下载完成后再次尝试
    return _BGE is not None


def _bge_embed(texts: list[str]) -> list[list[float]]:
    arr = _BGE.encode(texts, normalize_embeddings=True, batch_size=16)
    return [[float(x) for x in row] for row in arr]


def embed(texts: list[str], scope: str = "public") -> dict[str, Any]:
    """统一入口：服务端固定本地 BGE，失败降级本地 hash；无论 scope 都不外发。

    scope 保留在签名中兼容上层调用，但不再改变 embedding 后端：
    public / user 均使用同一本地 512 维口径，与 Qdrant 集合一致。
    """
    if scope not in ("public", "user"):
        scope = "user"  # 未知范围按最严格处理
    last_note = ""
    for backend in ("bge", "hash"):
        try:
            if backend == "bge":
                if not _bge_available():
                    last_note = "BGE 本地模型未安装或加载失败"
                    continue
                vectors = _bge_embed(texts)
                return _pack(vectors, "bge", True, False,
                             "服务端固定本地 BGE 512 维语义向量；embedding 全程不出本机")
            dim = cfg.integer("EMBED_DIM", 512)
            vectors = [_hash_vector(t, dim) for t in texts]
            return _pack(vectors, "local-hash-ngram", False, False,
                         "确定性字符 n-gram 投影（非语义向量）；与本地稀疏检索同口径")
        except Exception as exc:  # 不回显上游细节
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
    bge_ok = _bge_available()
    configured = cfg.get("EMBED_BACKEND", "bge").lower()
    # 服务端固定：bge（本地语义）→ hash（本地确定性投影）。用户无法选择 / 覆盖。
    effective = ("bge" if configured == "bge" and bge_ok else "local-hash-ngram")
    HASH = "local-hash-ngram"

    return {
        "ok": True,
        "configured_backend": configured,
        "server_fixed": True,
        "user_selectable": False,
        "bge_model": cfg.get("BGE_MODEL"),
        "bge_dim": cfg.integer("BGE_DIM", 512),
        "bge_available": bge_ok,
        "bge_package_installed": _bge_package_installed(),
        "bge_autoload": cfg.flag("BGE_AUTOLOAD", True),
        "bge_runtime": ("isolated:.bge_env（transformers 4.49.0 / ST 3.4.1，"
                        "避开主环境 4.x-5.x 混合损坏）" if _BGE_ENV.is_dir()
                        else "main-site-packages（独立目录不存在）"),
        "bge_model_source": ("local_hub_snapshot（按官方 modules.json 加载 CLS pooling）"
                             if _bge_model_path() != str(cfg.get("BGE_MODEL") or "")
                             else "huggingface_repo_id"),
        "dimension_policy": "服务端固定本地 BGE 512 维（集合 zhiyin_public_d_bge）；"
                            "不再使用云端 embedding-v3，避免维度不一致",
        "bge_reason": ("已加载本地权重（独立依赖目录）" if bge_ok and _BGE_ENV.is_dir() else
                       "已加载本地权重" if bge_ok else
                       "BGE_AUTOLOAD=false，未加载权重（避免健康检查触发下载）"
                       if not cfg.flag("BGE_AUTOLOAD", True) else
                       "sentence-transformers 未安装" if not _bge_package_installed() else
                       "本地权重或依赖加载失败，已自动降级（不冒充语义向量）"),
        "cloud_embedding_used": False,
        "hash_dim": cfg.integer("EMBED_DIM", 512),
        "selected_backend": {"public": effective, "user": effective},
        "semantic": {"bge": True, HASH: False},
        "user_data_stays_local": not cfg.flag("ALLOW_CLOUD_FOR_USER_DATA"),
        "note": "embedding 为服务器固定本地能力；hash 后端是确定性字符 n-gram 投影，"
                "不是语义向量，状态如实标注，不冒充 BGE",
    }
