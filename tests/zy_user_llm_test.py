#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""个人空间自带大模型 API（BYOK）回归：隔离 / 加密 / 配置边界 / 端点闸门。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import secure_config  # noqa: E402
import user_llm  # noqa: E402

PASS = FAIL = 0


def check(cond: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS %s" % label)
    else:
        FAIL += 1
        print("  FAIL %s %s" % (label, detail))


print("=== 1. 配置边界：embedding 服务器固定 ===")
providers = user_llm.providers_view()
check(providers["embedding"]["server_fixed"] is True
      and providers["embedding"]["user_selectable"] is False
      and providers["embedding"]["backend"] == "bge"
      and providers["embedding"]["dim"] == 512,
      "用户可见配置里 embedding 固定为本地 BGE 512，不可选择")
check(set(providers["providers"]) == {"qwen", "deepseek", "moonshot", "openai", "custom"},
      "只提供语言模型厂商预设，不提供 embedding 选项")
check("QWEN_EMBED_MODEL" not in secure_config.public_view()["keys"],
      "配置视图已移除 embedding-v3 残留项")
check(secure_config.DEFAULTS["QDRANT_COLLECTION"] == "zhiyin_public_d_bge"
      and secure_config.DEFAULTS["EMBED_BACKEND"] == "bge",
      "默认集合与 embedding 后端与实际配置一致")

print("=== 2. 身份隔离 / 加密落盘 ===")
alice, bob = "test:alice", "test:bob"
user_llm.delete(alice)
user_llm.delete(bob)
a = user_llm.save(alice, provider="qwen", api_key="sk-alice-secret-key-0001",
                  base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                  model="qwen-plus")
check(a["configured"] is True and a["provider"] == "qwen" and a["model"] == "qwen-plus",
      "保存后状态可用")
check("api_key" not in json.dumps(a, ensure_ascii=False)
      and "sk-alice" not in json.dumps(a, ensure_ascii=False),
      "状态视图不含 API key 值")
check(user_llm.public_view(bob)["configured"] is False,
      "不同身份之间配置隔离")
raw = user_llm.STORE_FILE.read_text(encoding="utf-8") if user_llm.STORE_FILE.is_file() else ""
check("sk-alice-secret-key-0001" not in raw and "dashscope.aliyuncs.com" not in raw,
      "落盘文件为密文，不含密钥与厂商端点明文")
check(user_llm.get(alice)["api_key"] == "sk-alice-secret-key-0001",
      "服务端内部可解密使用")
user_llm.delete(alice)
check(user_llm.public_view(alice)["configured"] is False, "删除后状态复位")

print("=== 3. 端点闸门（防 SSRF / 明文外发）===")
for bad in ("http://api.example.com/v1", "https://user:pass@api.example.com/v1",
            "https://169.254.169.254/latest", "https://10.0.0.1/v1", "ftp://x/y"):
    try:
        user_llm.validate_endpoint(bad)
        check(False, "拒绝不安全端点 %s" % bad)
    except user_llm.UserLLMError:
        check(True, "拒绝不安全端点 %s" % bad)
check(user_llm.validate_endpoint("https://api.deepseek.com/v1") == "https://api.deepseek.com/v1",
      "允许正常 https 兼容端点")

print("\n=== 个人 LLM 配置回归：PASS %d  FAIL %d ===" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
