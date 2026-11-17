#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""认证与分角色鉴权单元回归。

不绑定端口、不发真实网络请求：用 FastAPI TestClient 在进程内验证，
并通过 patch 强制 AUTH_MODE=required / 演示凭据 / WeChat 配置等状态，
覆盖：令牌签名与失效、state 一次性、管理员密码闸门、分角色写端点鉴权、
登出撤销、演示模式标注、真实 OAuth 端点形态校验。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import auth
import secure_config

PASS = 0
FAIL = 0


def check(cond: bool, name: str) -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS", name)
    else:
        FAIL += 1
        print("  FAIL", name)


print("=== 认证 / 分角色鉴权单元回归 ===")

# ---- 1. 令牌签名与校验 ----
t = auth.issue("personal", "wx:abc", "测试用户")
p = auth.verify(t)
check(p is not None and p["role"] == "personal" and p["name"] == "测试用户", "令牌签发与解析")
check(auth.verify(t + "tamper") is None, "篡改令牌被拒")
check(auth.verify(None) is None and auth.verify("") is None, "空令牌被拒")
expired = auth.issue("personal", "x")
check(auth.verify(expired) is not None, "正常令牌有效")
with patch.object(auth.cfg, "integer", side_effect=lambda k, d: -1 if k == "AUTH_SESSION_TTL" else d):
    old = auth.issue("personal", "x")
check(auth.verify(old) is None, "过期令牌被拒")

# ---- 2. 登出撤销 ----
t2 = auth.issue("admin", "admin")
check(auth.verify(t2) is not None, "撤销前有效")
auth.revoke(t2)
check(auth.verify(t2) is None, "登出撤销后失效")

# ---- 3. 状态 / CSRF state ----
w = auth.wechat_mode()
check(w["mode"] in ("real", "demo"), "微信模式取值合法")
check(isinstance(w["real_oauth_ready"], bool), "真实 OAuth 就绪标记是布尔")
s = auth.new_state("demo")
check(len(s) >= 16, "state 长度足够")
check(auth.demo_confirm(s)["demo"] is True, "演示确认返回演示身份")
try:
    auth.demo_confirm(s)
    check(False, "state 一次性（重放被拒）")
except auth.AuthError as e:
    check(e.code == "INVALID_STATE", "state 一次性（重放被拒）")
try:
    auth.consume_state(s, "real")
    check(False, "state 模式不匹配被拒")
except auth.AuthError as e:
    check(e.code == "INVALID_STATE", "state 模式不匹配被拒")

# ---- 4. 管理员密码闸门 ----
with patch("auth.secure_config.secret", side_effect=lambda k: None):
    with patch.object(auth.cfg, "flag", return_value=True):
        try:
            auth.admin_login("wrong", "127.0.0.1")
            check(False, "错误演示密码被拒")
        except auth.AuthError as e:
            check(e.code == "BAD_CREDENTIALS", "错误演示密码被拒")
        ok = auth.admin_login("zhiyin-admin-demo-2026", "127.0.0.1")
        check(ok["role"] == "admin" and ok["demo"] is True, "本机演示密码登录成功且标注演示")
        try:
            auth.admin_login("zhiyin-admin-demo-2026", "8.8.8.8")
            check(False, "远程使用演示凭据被拒")
        except auth.AuthError as e:
            check(e.code == "DEMO_REMOTE_DENIED", "远程使用演示凭据被拒")

# pbkdf2 哈希校验
h = auth.make_admin_hash("StrongPass2026")
check(h.startswith("pbkdf2_sha256$"), "密码哈希格式正确")
check(auth._verify_pw("StrongPass2026", h) and not auth._verify_pw("wrong", h), "密码哈希校验正确")
with patch("auth.secure_config.secret", side_effect=lambda k: h if k == "ADMIN_PASSWORD_HASH" else None):
    r = auth.admin_login("StrongPass2026", "8.8.8.8")
    check(r["role"] == "admin" and r["demo"] is False, "配置哈希后远程密码登录可用且非演示")

# ---- 5. 分角色 authorize（核心闸门） ----
with patch.object(auth, "mode", return_value="required"):
    try:
        auth.authorize(None, "admin")
        check(False, "required 模式匿名访问被拒")
    except auth.AuthError as e:
        check(e.code == "AUTH_REQUIRED", "required 模式匿名访问被拒")
    personal = auth.issue("personal", "p")
    try:
        auth.authorize(personal, "admin")
        check(False, "个人令牌访问管理员端点被拒")
    except auth.AuthError as e:
        check(e.code == "FORBIDDEN", "个人令牌访问管理员端点被拒")
    admin_t = auth.issue("admin", "a")
    check(auth.authorize(admin_t, "admin")["role"] == "admin", "管理员令牌访问管理员端点放行")
    check(auth.authorize(personal, "personal")["role"] == "personal", "个人令牌访问个人端点放行")
    check(auth.authorize(admin_t, "personal")["role"] == "admin", "管理员令牌兼容个人端点")

with patch.object(auth, "mode", return_value="optional"):
    check(auth.authorize(None, "admin") is None, "optional 模式匿名放行（本地回归兼容）")
    pt = auth.issue("personal", "p")
    try:
        auth.authorize(pt, "admin")
        check(False, "optional 模式带个人令牌仍拒管理员端点")
    except auth.AuthError:
        check(True, "optional 模式带个人令牌仍拒管理员端点")

# ---- 6. HTTP 层（TestClient，进程内，不发真实网络） ----
try:
    from fastapi.testclient import TestClient
    import server

    with TestClient(server.app) as client:
        # 页面与公开端点
        check(client.get("/login.html").status_code == 200, "登录页可访问")
        check(client.get("/api/auth/status").json()["ok"] is True, "auth/status 公开可读")
        check(client.get("/api/auth/session").json()["logged_in"] is False, "匿名会话显示未登录")

        # 演示扫码 → 个人 cookie
        st = client.post("/api/auth/wechat/authorize", json={}).json()["state"]
        r = client.post("/api/auth/wechat/demo/confirm", json={"state": st})
        check(r.status_code == 200 and r.json()["role"] == "personal", "演示扫码签发个人会话")
        check(client.get("/api/auth/session").json()["role"] == "personal", "个人会话 cookie 生效")

        # required 模式：中间件按角色拦截（用知识入库写端点做载体，传占位 id 不产生副作用）
        _ADMIN_WRITE = "/api/admin/knowledge/delete"
        _ADMIN_BODY = {"ids": ["auth-probe-no-op"]}
        with patch.object(auth, "mode", return_value="required"):
            anon = TestClient(server.app)
            resp = anon.post(_ADMIN_WRITE, json=_ADMIN_BODY)
            check(resp.status_code == 401 and resp.json().get("error") == "AUTH_REQUIRED",
                  "required：匿名写管理端点返回 401")

            personal_client = TestClient(server.app)
            st2 = personal_client.post("/api/auth/wechat/authorize", json={}).json()["state"]
            personal_client.post("/api/auth/wechat/demo/confirm", json={"state": st2})
            resp = personal_client.post(_ADMIN_WRITE, json=_ADMIN_BODY)
            check(resp.status_code == 403 and resp.json().get("error") == "FORBIDDEN",
                  "required：个人令牌写管理端点返回 403")

            # TestClient 的 client.host 非回环，演示凭据按设计不可用；注入真实哈希验证生产路径
            admin_hash = auth.make_admin_hash("StrongPass2026")
            def _secret(k):
                return admin_hash if k == "ADMIN_PASSWORD_HASH" else None
            with patch("auth.secure_config.secret", side_effect=_secret):
                admin_client = TestClient(server.app)
                lg = admin_client.post("/api/auth/admin/login", json={"password": "StrongPass2026"})
                check(lg.status_code == 200 and lg.json()["role"] == "admin" and lg.json()["demo"] is False,
                      "required：配置哈希后管理员密码登录成功（非演示）")
                resp = admin_client.post(_ADMIN_WRITE, json=_ADMIN_BODY)
                check(resp.status_code == 200, "required：管理员令牌写管理端点放行")

        # 登出
        client.post("/api/auth/logout", json={})
        check(client.get("/api/auth/session").json()["logged_in"] is False, "登出后会话失效")
except Exception as exc:
    check(False, "HTTP TestClient 用例执行异常：%s: %s" % (type(exc).__name__, exc))

print("\n=== 认证 / 分角色鉴权单元回归：PASS %d  FAIL %d ===" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
