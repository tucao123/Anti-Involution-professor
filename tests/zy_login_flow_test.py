#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""登录流程 HTTP 回归（FastAPI TestClient，进程内，不发真实网络）。

覆盖：强制登录路由（页面 302）、账号注册/登录/改密端点、GitHub 演示授权、
管理员独立密码入口、登录方式列表、登出。账号库隔离到临时文件。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from unittest.mock import patch

import local_accounts as la

PASS = 0
FAIL = 0


def check(cond, name):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS", name)
    else:
        FAIL += 1
        print("  FAIL", name)


print("=== 登录流程 HTTP 回归 ===")

_tmp = Path(tempfile.gettempdir()) / "zy_loginflow_accounts.json"
try:
    _tmp.unlink()
except OSError:
    pass
la.STORE_FILE = _tmp
la._FAILS.clear()

try:
    from fastapi.testclient import TestClient
    import server
    import auth

    with TestClient(server.app) as client:
        # 1. 登录方式列表
        pv = client.get("/api/auth/providers").json()
        check(pv["ok"] and set(pv["providers"]) >= {"local_accounts", "wechat", "github", "admin"},
              "providers 列出全部登录方式")
        check(pv["page_login_required"] is True, "页面强制登录默认开启")

        # 2. 页面强制登录：未登录访问工作台 302 到登录页并带回跳
        r = client.get("/workbench.html", follow_redirects=False)
        check(r.status_code == 302 and "/login.html?next=" in r.headers.get("location", ""),
              "未登录访问工作台 302 到登录页")
        check(client.get("/login.html", follow_redirects=False).status_code == 200, "登录页放行")
        # API 不被页面保护影响
        check(client.get("/api/auth/status").status_code == 200, "API 不受页面强制登录影响")

        # 3. 注册即登录
        reg = client.post("/api/auth/register", json={
            "identifier": "flowuser", "password": "StrongPass123", "name": "流程测试"})
        check(reg.status_code == 200 and reg.json()["role"] == "personal", "注册即登录为 personal")
        sess = client.get("/api/auth/session").json()
        check(sess["logged_in"] and sess["has_local_password"] and sess["provider"] == "local",
              "会话显示本地账号 / 有密码")
        # 登录后访问工作台放行
        check(client.get("/workbench.html", follow_redirects=False).status_code == 200,
              "登录后访问工作台放行")

        # 4. 登出 → 改密端点需登录
        client.post("/api/auth/logout", json={})
        chg = client.post("/api/auth/password/change", json={
            "old_password": "StrongPass123", "new_password": "NewPass1234"})
        check(chg.status_code == 401 and chg.json().get("error") == "AUTH_REQUIRED",
              "未登录改密被拒")

        # 5. 密码登录 + 改密
        lg = client.post("/api/auth/password/login", json={
            "identifier": "flowuser", "password": "StrongPass123"})
        check(lg.status_code == 200, "密码登录成功")
        chg = client.post("/api/auth/password/change", json={
            "old_password": "StrongPass123", "new_password": "NewPass1234"})
        check(chg.status_code == 200 and chg.json()["ok"], "登录后改密成功")
        client.post("/api/auth/logout", json={})
        check(client.post("/api/auth/password/login", json={
            "identifier": "flowuser", "password": "NewPass1234"}).status_code == 200,
            "新密码可登录")

        # 6. GitHub 演示授权：authorize → demo confirm
        gh = client.post("/api/auth/github/authorize", json={})
        check(gh.status_code == 200 and gh.json()["mode"] == "demo", "GitHub 未配置时降级演示")
        ghd = client.post("/api/auth/github/demo/confirm", json={"state": gh.json()["state"]})
        check(ghd.status_code == 200 and ghd.json()["demo"] is True, "GitHub 演示登录成功且标注演示")
        gs = client.get("/api/auth/session").json()
        check(gs["provider"] == "github", "会话识别为 GitHub 身份")
        # 第三方演示身份无本地密码
        chg = client.post("/api/auth/password/change", json={
            "old_password": "x", "new_password": "NewPass1234"})
        check(chg.status_code == 404, "第三方身份无本地密码，改密被拒")

        # 7. 微信演示登录仍可用
        wx = client.post("/api/auth/wechat/authorize", json={})
        wxd = client.post("/api/auth/wechat/demo/confirm", json={"state": wx.json()["state"]})
        check(wxd.status_code == 200 and wxd.json()["role"] == "personal", "微信演示登录仍可用")

        # 8. 管理员独立密码入口（本机 TestClient 演示凭据注入真实哈希）
        admin_hash = auth.make_admin_hash("AdminPass2026")
        with patch("auth.secure_config.secret", side_effect=lambda k: admin_hash if k == "ADMIN_PASSWORD_HASH" else None):
            client.post("/api/auth/logout", json={})
            ad = client.post("/api/auth/admin/login", json={"password": "AdminPass2026"})
            check(ad.status_code == 200 and ad.json()["role"] == "admin", "管理员独立密码登录成功")
            check(client.get("/api/auth/session").json()["role"] == "admin", "管理员会话生效")

        # 9. 登出
        client.post("/api/auth/logout", json={})
        check(client.get("/api/auth/session").json()["logged_in"] is False, "登出后会话失效")
except Exception as exc:
    check(False, "HTTP 流程用例异常：%s: %s" % (type(exc).__name__, exc))

try:
    _tmp.unlink()
except OSError:
    pass

print("\n=== 登录流程 HTTP 回归：PASS %d  FAIL %d ===" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
