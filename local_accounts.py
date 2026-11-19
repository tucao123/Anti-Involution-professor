#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""职引者 ZHIYIN · 本地账号体系（注册 / 密码登录 / 改密）

设计原则（与全项目数据边界一致）：
1. 账号只用于登录鉴权，不产出任何用户画像：记录仅含登录标识（用户名 / 邮箱）、
   密码哈希、昵称、创建/改密时间、sub。不做画像、不入向量库、不进 RAG、不外发。
2. 邮箱 / 昵称等字段以 AES-256-GCM 加密落盘在 .privacy/accounts.json（gitignored）；
   磁盘上只写不可逆的查找索引（标识 -> slot），不写明文账号、不写明文密码。
3. 密码哈希用标准库 pbkdf2_hmac（180000 次）逐账号随机盐，恒定时间比较；
   明文密码只在请求内存里出现，绝不写盘 / 打印 / 进错误响应。
4. 注册账号恒为 personal 角色；管理员只能走 auth.admin_login 独立密码入口，
   本模块没有任何自我提权到 admin 的路径。
5. 防暴力破解：进程内按标识计数，连续失败 5 次锁定 60 秒；成功即清零。
6. 加密密钥优先取 AUTH_SECRET / ZHIYIN_USER_LLM_KEY；无则进程内随机，
   重启后账号不可读（status 如实标注 persistent=false）。正式部署请配 AUTH_SECRET。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import auth
import secure_config

ROOT = Path(__file__).resolve().parent
STORE_DIR = ROOT / ".privacy"
STORE_FILE = STORE_DIR / "accounts.json"
_LOCK = threading.RLock()

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,32}$")
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

_MAX_FAILS = 5
_LOCK_SECONDS = 60
_FAILS: dict[str, tuple[int, float]] = {}  # slot -> (失败次数, 锁定截止时间戳)


class AccountError(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _load_key() -> tuple[bytes, str, bool]:
    raw = (os.environ.get("ZHIYIN_USER_LLM_KEY")
           or secure_config.secret("AUTH_SECRET") or "").strip()
    if raw:
        try:
            decoded = bytes.fromhex(raw)
            if len(decoded) == 32:
                return decoded, "env:hex32", True
        except ValueError:
            pass
        return hashlib.sha256(raw.encode("utf-8")).digest(), "env:derived", True
    return AESGCM.generate_key(bit_length=256), "ephemeral:process-memory", False


_KEY, _KEY_SOURCE, _PERSISTENT = _load_key()
_AES = AESGCM(_KEY)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalize(identifier: str) -> str:
    value = (identifier or "").strip().lower()
    return value


def _slot(identifier: str) -> str:
    """登录标识 -> 不可逆索引；明文账号永不落盘。"""
    return hashlib.sha256(("acct:" + _normalize(identifier)).encode("utf-8")).hexdigest()[:24]


def _is_email(value: str) -> bool:
    return bool(_EMAIL_RE.match(value))


def _read() -> dict[str, Any]:
    if not STORE_FILE.is_file():
        return {}
    try:
        data = json.loads(STORE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(data: dict[str, Any]) -> None:
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STORE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STORE_FILE)


def _encrypt(record: dict[str, Any], slot: str) -> str:
    nonce = secrets.token_bytes(12)
    body = json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ct = _AES.encrypt(nonce, body, slot.encode("ascii"))
    return base64.b64encode(nonce + ct).decode("ascii")


def _decrypt(blob: str, slot: str) -> dict[str, Any] | None:
    try:
        raw = base64.b64decode(blob)
        out = json.loads(_AES.decrypt(raw[:12], raw[12:], slot.encode("ascii")))
        return out if isinstance(out, dict) else None
    except Exception:
        return None


# 过期条目清理节流：避免每次登录都全量扫描，最多每 60 秒清一次
_LAST_PRUNE = 0.0


def _prune_fails(now: float) -> None:
    global _LAST_PRUNE
    if now - _LAST_PRUNE < 60:
        return
    _LAST_PRUNE = now
    for key, (_cnt, until) in list(_FAILS.items()):
        # 锁定已过期且距最后尝试很久的项直接丢弃，防止字典无界增长
        if until and until <= now:
            _FAILS.pop(key, None)


def _guard(slot: str) -> None:
    now = time.time()
    _prune_fails(now)
    cnt, until = _FAILS.get(slot, (0, 0.0))
    if until > now:
        raise AccountError("TOO_MANY_ATTEMPTS", "尝试次数过多，请稍后再试", 429)


def _fail(slot: str) -> None:
    cnt, _ = _FAILS.get(slot, (0, 0.0))
    cnt += 1
    _FAILS[slot] = (cnt, time.time() + _LOCK_SECONDS if cnt >= _MAX_FAILS else 0.0)


def _reset(slot: str) -> None:
    _FAILS.pop(slot, None)


def validate_new_account(identifier: str, password: str) -> tuple[str, bool]:
    """返回 (规范化标识, 是否邮箱)。用户名 3-32 位字母数字下划线，或合法邮箱。"""
    norm = _normalize(identifier)
    if not norm:
        raise AccountError("BAD_IDENTIFIER", "请输入用户名或邮箱")
    if _is_email(norm):
        if len(norm) > 120:
            raise AccountError("BAD_IDENTIFIER", "邮箱过长")
        return norm, True
    if not _USERNAME_RE.match(norm):
        raise AccountError("BAD_IDENTIFIER", "用户名须为 3-32 位字母、数字或下划线")
    if not (8 <= len(password) <= 128):
        raise AccountError("WEAK_PASSWORD", "密码须为 8-128 位", 400)
    return norm, False


def register(identifier: str, password: str, name: str = "") -> dict[str, Any]:
    """注册本地账号，恒为 personal 角色。返回可供issue()使用的身份字典。"""
    if not secure_config.cfg.flag("AUTH_LOCAL_ACCOUNTS", True):
        raise AccountError("LOCAL_ACCOUNTS_DISABLED", "本地账号注册已关闭", 403)
    norm, is_email = validate_new_account(identifier, password)
    if not (8 <= len(password) <= 128):
        raise AccountError("WEAK_PASSWORD", "密码须为 8-128 位")
    slot = _slot(norm)
    with _LOCK:
        data = _read()
        if slot in data:
            raise AccountError("IDENTIFIER_TAKEN", "该用户名或邮箱已被注册", 409)
        sub = "acct:" + hashlib.sha256(("sub:" + slot).encode("ascii")).hexdigest()[:16]
        record = {
            "identifier": norm,
            "is_email": is_email,
            "name": (name or "").strip()[:40],
            "pw_hash": auth._hash_pw(password),
            "sub": sub,
            "created_at": _now(),
            "updated_at": _now(),
        }
        data[slot] = _encrypt(record, slot)
        _write(data)
    _reset(slot)
    return {"role": auth.ROLE_PERSONAL, "sub": sub, "name": record["name"] or norm, "demo": False}


def login(identifier: str, password: str) -> dict[str, Any]:
    """用户名或邮箱 + 密码登录。失败统一返回 BAD_CREDENTIALS，不区分账号是否存在。"""
    if not secure_config.cfg.flag("AUTH_LOCAL_ACCOUNTS", True):
        raise AccountError("LOCAL_ACCOUNTS_DISABLED", "本地账号登录已关闭", 403)
    slot = _slot(identifier)
    _guard(slot)
    with _LOCK:
        blob = _read().get(slot)
    record = _decrypt(blob, slot) if blob else None
    if not record or not auth._verify_pw(password, record.get("pw_hash", "")):
        _fail(slot)
        raise AccountError("BAD_CREDENTIALS", "账号或密码错误", 401)
    _reset(slot)
    return {"role": auth.ROLE_PERSONAL, "sub": record["sub"],
            "name": record.get("name") or record["identifier"], "demo": False}


def change_password(sub: str, old_password: str, new_password: str) -> dict[str, Any]:
    """改密：须提供正确的当前密码。新密码 8-128 位。"""
    if not (8 <= len(new_password) <= 128):
        raise AccountError("WEAK_PASSWORD", "新密码须为 8-128 位")
    with _LOCK:
        data = _read()
        target_slot = None
        target_record = None
        for slot, blob in data.items():
            rec = _decrypt(blob, slot)
            if rec and rec.get("sub") == sub:
                target_slot, target_record = slot, rec
                break
        if not target_slot or not target_record:
            raise AccountError("ACCOUNT_NOT_FOUND", "未找到该登录账号（第三方登录无本地密码）", 404)
        if not auth._verify_pw(old_password, target_record.get("pw_hash", "")):
            _fail(target_slot)
            raise AccountError("BAD_CREDENTIALS", "当前密码不正确", 401)
        target_record["pw_hash"] = auth._hash_pw(new_password)
        target_record["updated_at"] = _now()
        data[target_slot] = _encrypt(target_record, target_slot)
        _write(data)
    _reset(target_slot)
    return {"ok": True, "changed_at": target_record["updated_at"]}


def status() -> dict[str, Any]:
    with _LOCK:
        count = len(_read())
    return {
        "enabled": secure_config.cfg.flag("AUTH_LOCAL_ACCOUNTS", True),
        "accounts": count,
        "persistent": _PERSISTENT,
        "key_source": _KEY_SOURCE,
        "password_hash": "pbkdf2_hmac-sha256",
    }
