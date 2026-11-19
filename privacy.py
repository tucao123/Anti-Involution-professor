#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
职引者 ZHIYIN · PIPL 合规基础设施（文档 §6.1，合规前置）

已实现：
- 字段级 AES-256-GCM 加密；密钥只来自环境变量或进程内存，绝不写入项目文件。
- 展示脱敏（姓名 / 手机 / 邮箱 / 身份证）；明文只在显式 reveal 时于服务端进程内短暂存在。
- 一键删除 + 90 天自动过期；密文与审计日志分离存放。
- 不可篡改审计：append-only JSONL 哈希链，每次访问/导出/删除/拦截都留痕，可校验链完整性。
- LLM 前置 PII 拦截：检测到个人信息的内容一律不外发到云端模型。

2026-11-16 变更：B1 简历上传 / 解析已按项目所有者回执开放，但**不在本模块入库**。
`resume_parse.py` 只在内存解析 pdf / docx，只输出粗粒度岗位信息，文件与原文解析完即释放；
个人信息不提取、不入库；个人画像由用户主动提供 JD / 简历的本地 `/api/jd/analyze` 链路提供。
撤回与删除入口见 `/api/resume/withdraw`、`/api/resume/delete`。PII 入库入口仍不开放。
传输层 TLS 1.3 由部署侧反向代理提供；本地开发仅绑定 127.0.0.1。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = Path(__file__).resolve().parent
VAULT_DIR = ROOT / ".privacy"
VAULT_FILE = VAULT_DIR / "vault.json"
AUDIT_FILE = VAULT_DIR / "audit.jsonl"
RETENTION_DAYS = 90
_LOCK = threading.RLock()

PII_FIELDS = ("name", "phone", "email", "id_card")

PII_PATTERNS = {
    "phone": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    "id_card": re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
}


def _load_key() -> tuple[bytes, str]:
    """密钥来源：统一配置（进程环境变量 > .env，hex 32 字节 / 任意口令派生）或进程内随机；都不落盘。"""
    raw = ""
    try:
        import secure_config
        raw = secure_config.secret("ZHIYIN_PII_KEY") or ""
    except Exception:
        pass
    if not raw:
        raw = os.environ.get("ZHIYIN_PII_KEY")
    if raw:
        try:
            decoded = bytes.fromhex(raw)
            if len(decoded) == 32:
                return decoded, "env:hex32"
        except ValueError:
            pass
        return hashlib.sha256(raw.encode("utf-8")).digest(), "env:derived"
    return AESGCM.generate_key(bit_length=256), "ephemeral:process-memory"


KEY, KEY_SOURCE = _load_key()
_AES = AESGCM(KEY)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ensure_dir() -> None:
    VAULT_DIR.mkdir(parents=True, exist_ok=True)


def _read_vault() -> dict[str, Any]:
    if not VAULT_FILE.is_file():
        return {}
    try:
        data = json.loads(VAULT_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_vault(data: dict[str, Any]) -> None:
    _ensure_dir()
    tmp = VAULT_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(VAULT_FILE)


def _last_hash() -> str:
    if not AUDIT_FILE.is_file():
        return "0" * 64
    try:
        lines = [ln for ln in AUDIT_FILE.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if not lines:
            return "0" * 64
        return str(json.loads(lines[-1]).get("hash") or "0" * 64)
    except (OSError, ValueError):
        return "0" * 64


def _canonical(entry: dict[str, Any]) -> str:
    return json.dumps(entry, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def audit(action: str, record_id: str = "", detail: str = "") -> dict[str, Any]:
    """append-only 哈希链；日志只含动作、记录 id 与脱敏说明，不含任何明文 PII 或密钥。"""
    with _LOCK:
        _ensure_dir()
        seq = 0
        if AUDIT_FILE.is_file():
            try:
                seq = sum(1 for ln in AUDIT_FILE.read_text(encoding="utf-8").splitlines() if ln.strip())
            except OSError:
                seq = 0
        entry = {
            "seq": seq,
            "ts": _now(),
            "action": action,
            "record": record_id,
            "detail": detail[:200],
            "prev": _last_hash(),
        }
        entry["hash"] = hashlib.sha256(_canonical(entry).encode("utf-8")).hexdigest()
        with AUDIT_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry


def verify_chain() -> tuple[bool, int, int | None]:
    """重算每条哈希并校验 prev 链接；被篡改会返回首个断裂序号。"""
    if not AUDIT_FILE.is_file():
        return True, 0, None
    prev = "0" * 64
    try:
        lines = [ln for ln in AUDIT_FILE.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except OSError:
        return True, 0, None
    for ln in lines:
        try:
            entry = json.loads(ln)
        except ValueError:
            return False, len(lines), None
        # 只摘掉 hash 本身；prev 是写入摘要时的一部分，必须保留参与重算
        expected_hash = entry.pop("hash", None)
        expected_prev = entry.get("prev")
        if expected_prev != prev:
            return False, len(lines), entry.get("seq")
        if hashlib.sha256(_canonical(entry).encode("utf-8")).hexdigest() != expected_hash:
            return False, len(lines), entry.get("seq")
        prev = expected_hash
    return True, len(lines), None


def mask(field: str, value: str) -> str:
    v = str(value or "")
    if field == "phone":
        return re.sub(r"(\d{3})\d{4}(\d{4})", r"\1****\2", v)
    if field == "id_card":
        return v[:6] + "*" * 8 + v[-4:] if len(v) >= 14 else "***"
    if field == "email":
        local, _, domain = v.partition("@")
        return (local[:1] + "***@" + domain) if domain else "***"
    if field == "name":
        return (v[:1] + "*" * max(1, len(v) - 1)) if v else "***"
    return "***"


def _encrypt(value: str) -> dict[str, str]:
    nonce = os.urandom(12)
    cipher = _AES.encrypt(nonce, value.encode("utf-8"), None)
    return {"alg": "AES-256-GCM", "n": base64.b64encode(nonce).decode(),
            "c": base64.b64encode(cipher).decode()}


def _decrypt(obj: dict[str, str]) -> str | None:
    try:
        return _AES.decrypt(base64.b64decode(obj["n"]), base64.b64decode(obj["c"]), None).decode("utf-8")
    except Exception:
        # 进程内随机密钥重启后旧密文不可解：按不可恢复数据处理，不回显错误细节
        return None


def scan_pii(payload: Any) -> list[str]:
    """LLM 前置拦截：payload 中出现个人信息即拒绝外发。"""
    try:
        text = json.dumps(payload, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(payload)
    return [name for name, pattern in PII_PATTERNS.items() if pattern.search(text)]


def purge_expired() -> int:
    with _LOCK:
        vault = _read_vault()
        now = datetime.now(timezone.utc)
        expired = []
        for rid, rec in vault.items():
            try:
                if datetime.fromisoformat(str(rec.get("expires_at"))) < now:
                    expired.append(rid)
            except (ValueError, TypeError):
                expired.append(rid)
        for rid in expired:
            vault.pop(rid, None)
            audit("auto_expired", rid, "超过留存期，密文已删除")
        if expired:
            _write_vault(vault)
        return len(expired)


def store_record(fields: dict[str, str], meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """只接受白名单 PII 字段；落盘的是密文 + 脱敏预览，不存明文。"""
    clean = {k: str(v) for k, v in (fields or {}).items() if k in PII_FIELDS and v}
    if not clean:
        raise ValueError("NO_PII_FIELD")
    rid = secrets.token_hex(8)
    now = datetime.now(timezone.utc)
    record = {
        "id": rid,
        "created_at": now.isoformat(timespec="seconds"),
        "expires_at": (now + timedelta(days=RETENTION_DAYS)).isoformat(timespec="seconds"),
        "fields": {k: _encrypt(v) for k, v in clean.items()},
        "masked": {k: mask(k, v) for k, v in clean.items()},
        "meta": {k: str(v)[:120] for k, v in (meta or {}).items()},
    }
    with _LOCK:
        vault = _read_vault()
        vault[rid] = record
        _write_vault(vault)
        audit("store", rid, "字段级加密入库：" + ",".join(sorted(clean)))
    return {"id": rid, "masked": record["masked"], "expires_at": record["expires_at"]}


def read_record(rid: str, reveal: bool = False) -> dict[str, Any] | None:
    with _LOCK:
        purge_expired()
        rec = _read_vault().get(rid)
        if not rec:
            return None
        audit("reveal" if reveal else "view_masked", rid, "读取" + ("（显式解密）" if reveal else "（脱敏视图）"))
        out = {
            "id": rid,
            "masked": rec.get("masked", {}),
            "created_at": rec.get("created_at"),
            "expires_at": rec.get("expires_at"),
            "meta": rec.get("meta", {}),
        }
        if reveal:
            out["fields"] = {k: _decrypt(v) for k, v in (rec.get("fields") or {}).items()}
        return out


def delete_record(rid: str) -> bool:
    with _LOCK:
        vault = _read_vault()
        if rid not in vault:
            return False
        vault.pop(rid, None)
        _write_vault(vault)
        audit("delete", rid, "用户一键删除，密文已移除")
        return True


def status() -> dict[str, Any]:
    # B1 是否开放由书面回执闸门实时推导，不缓存、不手工硬编码
    try:
        import approval_gate
        b1 = approval_gate.gate("B1")
        resume_open = b1["enabled"]
        resume_legal = b1["legal_review"]
    except Exception:
        resume_open, resume_legal = False, "pending"
    with _LOCK:
        purge_expired()
        ok, count, first_bad = verify_chain()
        return {
            "ok": True,
            "cipher": "AES-256-GCM",
            "key_source": KEY_SOURCE,
            "key_persisted": False,
            "retention_days": RETENTION_DAYS,
            "records": len(_read_vault()),
            "pii_ingest_open": False,
            "resume_parse_open": resume_open,
            "resume_parse_legal_review": resume_legal,
            "resume_parse_retention": "none（文件 / 原文仅内存，不入库）",
            "resume_withdraw_endpoint": "/api/resume/withdraw",
            "resume_delete_endpoint": "/api/resume/delete",
            "screenshot_pii_to_store": False,
            "llm_pii_guard": True,
            "audit": {"entries": count, "chain_ok": ok, "first_bad_seq": first_bad},
            "note": "简历解析按 B1 回执开放（仅岗位信息、不留存、可撤回删除）；"
                    "PII 入库入口仍关闭，含 PII 内容一律不外发至云端模型。"
                    "截图 / 爬取链路的隐私信息整行剔除、不入库；"
                    "个人画像走用户主动提供数据的本地 /api/jd/analyze 链路。",
        }
