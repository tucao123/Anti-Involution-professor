#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""职引者 ZHIYIN · 认证与分角色鉴权（研究学习原型）

设计原则（与全项目数据边界一致）：
1. 凭据只留在进程内存：微信 appsecret / AUTH_SECRET / 管理员密码哈希均来自 secure_config，
   不写入任何项目文件、不打印到日志、不回显给浏览器（public_view 只报是否配置）。
2. 微信扫码走开放平台标准 OAuth2（qrconnect → code → access_token → userinfo）。
   未配置 AppID/Secret/回调域名时，明确降级为「演示扫码」：本地生成 state + 模拟二维码，
   用户点「确认扫码」后签发【演示身份】，并在每个响应里标注 demo=true，绝不伪装成真实微信。
3. 会话是 HMAC 签名的无状态令牌（base64url(payload).hex(hmac)），存 httpOnly cookie；
   openid 只保留截断哈希，不落盘、不进画像、不入向量库。登出用进程内 nonce 撤销表。
4. 管理员只能密码登录：密码用 pbkdf2_sha256 哈希校验（config.compare_digest），
   未配置 ADMIN_PASSWORD_HASH 时开放一个「本机演示凭据」，且仅允许回环地址使用。
5. 分角色：personal 可访问普通写端点；admin 才可访问管理写端点。
   AUTH_MODE=off 不校验；optional（默认，本地研究/回归）带令牌则严格校验角色、无令牌放行；
   required（正式部署）所有受保护写端点必须带有效令牌且角色匹配。

本模块不依赖数据库 / Redis，缺失任何可选组件都可运行。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import urllib.request
from typing import Any

import secure_config

cfg = secure_config.cfg

ROLE_PERSONAL = "personal"
ROLE_ADMIN = "admin"
COOKIE_NAME = "zy_session"

# 演示凭据（仅本机、仅未配置 ADMIN_PASSWORD_HASH 时生效）。只存哈希，不存明文常量之外的用途。
_DEMO_ADMIN_PASSWORD = "zhiyin-admin-demo-2026"

# ---- 进程内状态（重启即失效，正是「凭据只在内存」的要求） ----
_SECRET = (secure_config.secret("AUTH_SECRET") or os.environ.get("AUTH_SECRET") or "").strip()
if not _SECRET:
    _SECRET = secrets.token_hex(32)      # 未配置则每次启动随机，重启后所有会话失效
    _SECRET_EPHEMERAL = True
else:
    _SECRET_EPHEMERAL = False

_STATES: dict[str, dict[str, Any]] = {}          # state -> {exp, mode}
_REVOKED: set[str] = set()                        # 登出后的 token nonce
_STATE_TTL = 600                                  # 扫码 state 10 分钟
_MAX_STATES = 256


class AuthError(Exception):
    """认证层可预期错误，携带稳定的机器码。"""

    def __init__(self, code: str, message: str, status: int = 401) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


# ---------------------------------------------------------------------------
# 会话令牌：HMAC 签名，无状态，可校验完整性与有效期
# ---------------------------------------------------------------------------
def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(payload: dict[str, Any]) -> str:
    body = _b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    sig = hmac.new(_SECRET.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
    return body + "." + sig


def issue(role: str, sub: str, name: str = "", demo: bool = False) -> str:
    now = int(time.time())
    return _sign({
        "role": role,
        "sub": sub,
        "name": name[:40],
        "iat": now,
        "exp": now + cfg.integer("AUTH_SESSION_TTL", 86400),
        "demo": bool(demo),
        "nonce": secrets.token_hex(8),
    })


def verify(token: str | None) -> dict[str, Any] | None:
    if not token or not isinstance(token, str) or "." not in token:
        return None
    body, _, sig = token.partition(".")
    expect = hmac.new(_SECRET.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expect):
        return None
    try:
        payload = json.loads(_b64d(body))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if int(payload.get("exp", 0)) <= int(time.time()):
        return None
    if payload.get("nonce") in _REVOKED:
        return None
    if payload.get("role") not in (ROLE_PERSONAL, ROLE_ADMIN):
        return None
    return payload


def revoke(token: str | None) -> None:
    payload = verify(token)
    if payload and payload.get("nonce"):
        _REVOKED.add(payload["nonce"])
        if len(_REVOKED) > 2048:
            _REVOKED.clear()


def _hash_pw(password: str, iterations: int = 180_000, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "pbkdf2_sha256$%d$%s$%s" % (iterations, salt.hex(), digest.hex())


def _verify_pw(password: str, stored: str) -> bool:
    """校验 pbkdf2_sha256 哈希；恒定时间比较，永不回显哈希或明文。"""
    try:
        scheme, iters, salt_hex, digest_hex = stored.strip().split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                     bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(digest.hex(), digest_hex)
    except Exception:
        return False


def make_admin_hash(password: str) -> str:
    """供离线生成 ADMIN_PASSWORD_HASH：python -c 'import auth;print(auth.make_admin_hash("..."))'"""
    if not password or len(password) < 8:
        raise AuthError("WEAK_PASSWORD", "管理员密码至少 8 位", 400)
    return _hash_pw(password)


# ---------------------------------------------------------------------------
# 微信 OAuth：真实流程 + 演示降级
# ---------------------------------------------------------------------------
def _wechat_config() -> dict[str, str]:
    return {
        "appid": (secure_config.secret("WECHAT_APPID") or "").strip(),
        "secret": (secure_config.secret("WECHAT_APPSECRET") or "").strip(),
        "redirect": (secure_config.secret("WECHAT_REDIRECT_URI") or "").strip(),
    }


def wechat_mode() -> dict[str, Any]:
    """返回当前微信登录形态。real=配置齐全走开放平台；demo=本地演示，明确标注。"""
    c = _wechat_config()
    real = bool(c["appid"] and c["secret"] and c["redirect"]
                and c["redirect"].startswith("https://"))
    demo_allowed = cfg.flag("AUTH_DEMO_ENABLED", True)
    return {
        "mode": "real" if real else "demo",
        "real_oauth_ready": real,
        "demo_allowed": demo_allowed,
        "configured": {
            "appid": bool(c["appid"]),
            "appsecret": bool(c["secret"]),
            "redirect_uri": bool(c["redirect"]),
        },
        "reason": ("微信开放平台配置齐全，走标准扫码登录" if real else
                   "未配置 WECHAT_APPID / WECHAT_APPSECRET / WECHAT_REDIRECT_URI(https)，"
                   "降级为本地演示扫码（非真实微信登录）"),
    }


def _gc_states() -> None:
    now = int(time.time())
    for k in [k for k, v in _STATES.items() if v["exp"] <= now]:
        _STATES.pop(k, None)
    while len(_STATES) > _MAX_STATES:
        _STATES.pop(next(iter(_STATES)))


def new_state(mode: str) -> str:
    _gc_states()
    state = secrets.token_urlsafe(24)
    _STATES[state] = {"exp": int(time.time()) + _STATE_TTL, "mode": mode}
    return state


def consume_state(state: str, expect_mode: str | None = None) -> dict[str, Any]:
    rec = _STATES.pop(state, None)
    if not rec or rec["exp"] <= int(time.time()):
        raise AuthError("INVALID_STATE", "扫码状态无效或已过期，请重新开始", 400)
    if expect_mode and rec["mode"] != expect_mode:
        raise AuthError("STATE_MODE_MISMATCH", "扫码状态模式不匹配", 400)
    return rec


def authorize_url(state: str) -> str:
    c = _wechat_config()
    if not wechat_mode()["real_oauth_ready"]:
        raise AuthError("WECHAT_NOT_CONFIGURED", "微信开放平台未配置，不能生成真实授权 URL", 503)
    from urllib.parse import urlencode
    qs = urlencode({
        "appid": c["appid"],
        "redirect_uri": c["redirect"],
        "response_type": "code",
        "scope": "snsapi_login",
        "state": state,
    })
    return "https://open.weixin.qq.com/connect/qrconnect?" + qs + "#wechat_redirect"


def _http_json(url: str, timeout: int = 8) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def exchange_code(code: str, state: str) -> dict[str, Any]:
    """真实 OAuth：code 换 token → 拉 userinfo。只保留 openid 截断哈希与昵称（会话内展示）。"""
    consume_state(state, "real")
    c = _wechat_config()
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", code or ""):
        raise AuthError("INVALID_CODE", "微信回调 code 形态非法", 400)
    from urllib.parse import urlencode
    token_url = ("https://api.weixin.qq.com/sns/oauth2/access_token?" + urlencode({
        "appid": c["appid"], "secret": c["secret"], "code": code,
        "grant_type": "authorization_code",
    }))
    try:
        tok = _http_json(token_url)
    except Exception as exc:
        raise AuthError("WECHAT_TOKEN_FAILED", "微信换取 access_token 失败：%s" % type(exc).__name__, 502) from exc
    openid = str(tok.get("openid") or "")
    access = str(tok.get("access_token") or "")
    if not openid or not access:
        raise AuthError("WECHAT_TOKEN_INVALID", "微信未返回 openid / access_token", 502)
    info: dict[str, Any] = {}
    try:
        info = _http_json("https://api.weixin.qq.com/sns/userinfo?" + urlencode({
            "access_token": access, "openid": openid, "lang": "zh_CN",
        }))
    except Exception:
        pass  # userinfo 失败不影响登录，昵称留空
    sub = "wx:" + hashlib.sha256(openid.encode("utf-8")).hexdigest()[:16]
    name = str(info.get("nickname") or "")[:40]
    return {"role": ROLE_PERSONAL, "sub": sub, "name": name, "demo": False}


def demo_confirm(state: str) -> dict[str, Any]:
    """演示扫码：校验 state 后签发【演示个人身份】，与真实微信严格区分。"""
    if not cfg.flag("AUTH_DEMO_ENABLED", True):
        raise AuthError("DEMO_DISABLED", "演示扫码已关闭", 403)
    consume_state(state, "demo")
    return {"role": ROLE_PERSONAL, "sub": "demo: personal", "name": "演示用户", "demo": True}


# ---------------------------------------------------------------------------
# 管理员密码登录
# ---------------------------------------------------------------------------
def admin_status() -> dict[str, Any]:
    configured = bool((secure_config.secret("ADMIN_PASSWORD_HASH") or "").strip())
    return {
        "password_source": "env-hash" if configured else "local-demo",
        "demo_credential": not configured,
        "loopback_only_demo": not configured,
        "reason": ("管理员密码哈希来自 ADMIN_PASSWORD_HASH" if configured else
                   "未配置 ADMIN_PASSWORD_HASH，使用本机演示凭据（仅 127.0.0.1 / ::1 可用，"
                   "正式部署必须配置哈希）"),
    }


def admin_login(password: str, client_host: str = "") -> dict[str, Any]:
    if not isinstance(password, str) or not (1 <= len(password) <= 128):
        raise AuthError("INVALID_PASSWORD", "密码格式非法", 400)
    stored = (secure_config.secret("ADMIN_PASSWORD_HASH") or "").strip()
    if not stored:
        host = (client_host or "").strip()
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise AuthError("DEMO_REMOTE_DENIED", "演示凭据仅允许本机使用；请配置 ADMIN_PASSWORD_HASH", 403)
        if not cfg.flag("AUTH_DEMO_ENABLED", True):
            raise AuthError("DEMO_DISABLED", "演示登录已关闭", 403)
        if not hmac.compare_digest(password, _DEMO_ADMIN_PASSWORD):
            raise AuthError("BAD_CREDENTIALS", "密码错误", 401)
        return {"role": ROLE_ADMIN, "sub": "admin:local-demo", "name": "管理员（演示）", "demo": True}
    if not _verify_pw(password, stored):
        raise AuthError("BAD_CREDENTIALS", "密码错误", 401)
    return {"role": ROLE_ADMIN, "sub": "admin", "name": "管理员", "demo": False}


# ---------------------------------------------------------------------------
# 鉴权闸门：供 server.py 的 Depends / 中间件调用
# ---------------------------------------------------------------------------
def mode() -> str:
    value = cfg.get("AUTH_MODE", "optional").strip().lower()
    return value if value in ("off", "optional", "required") else "optional"


def identity(token: str | None) -> dict[str, Any] | None:
    return verify(token)


def authorize(token: str | None, need_role: str | None = None) -> dict[str, Any] | None:
    """返回身份或 None 表示放行；需要角色时用 AuthError 表示明确拒绝。"""
    m = mode()
    ident = verify(token)
    if m == "off":
        return ident
    if m == "required" and ident is None:
        raise AuthError("AUTH_REQUIRED", "需要登录后再操作", 401)
    if need_role:
        if ident is None:
            if m == "required":
                raise AuthError("AUTH_REQUIRED", "需要登录后再操作", 401)
            return None  # optional：无令牌沿用历史行为，便于本地研究与回归
        role = ident.get("role")
        # 管理员是个人能力的超集：admin 既可进管理后台，也可用个人写端点。
        allowed = (role == need_role) or (role == ROLE_ADMIN)
        if not allowed:
            raise AuthError("FORBIDDEN", "当前身份无权执行此操作", 403)
    return ident


def status() -> dict[str, Any]:
    return {
        "ok": True,
        "auth_mode": mode(),
        "session_ttl": cfg.integer("AUTH_SESSION_TTL", 86400),
        "secret_ephemeral": _SECRET_EPHEMERAL,
        "wechat": wechat_mode(),
        "admin": admin_status(),
        "roles": [ROLE_PERSONAL, ROLE_ADMIN],
        "cookie_name": COOKIE_NAME,
    }
