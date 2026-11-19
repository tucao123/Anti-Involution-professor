#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""路由域：认证与会话（拆分自 server.py，行为保持一致）。"""
from __future__ import annotations

from fastapi import APIRouter
from .common import *  # noqa: F401,F403
from .common import (_optional, _metrics, _set_session_cookie, _require_identity,
                     _privacy_identity, _privacy_owns, _need)  # noqa: F401

router = APIRouter()

@router.post("/api/auth/admin/login")
def auth_admin_login(req: AuthAdminLoginRequest, request: Request):
    host = request.client.host if request.client else ""
    ident = auth.admin_login(req.password, host)
    token = auth.issue(ident["role"], ident["sub"], ident["name"], demo=ident["demo"])
    body: dict[str, Any] = {"ok": True, "logged_in": True, "role": ident["role"],
                            "name": ident["name"], "demo": ident["demo"]}
    if ident["demo"]:
        body["notice"] = "演示凭据登录，正式部署请配置 ADMIN_PASSWORD_HASH"
    r = JSONResponse(content=body)
    _set_session_cookie(r, token)
    return r

@router.post("/api/auth/github/authorize")
def auth_github_authorize():
    gm = auth.github_mode()
    state = auth.new_state("github-real" if gm["real_oauth_ready"] else "github-demo")
    if gm["real_oauth_ready"]:
        return {"ok": True, "mode": "real", "authorize_url": auth.github_authorize_url(state),
                "state": state}
    if not gm["demo_allowed"]:
        return fail(403, "DEMO_DISABLED", "演示授权已关闭，且未配置 GitHub OAuth 凭据")
    return {"ok": True, "mode": "demo", "authorize_url": None, "state": state,
            "demo_notice": "未配置 GitHub OAuth 凭据，当前为本地演示授权，非真实 GitHub 登录"}

@router.post("/api/auth/github/callback")
def auth_github_callback(req: AuthWechatCallbackRequest):
    # 复用 code+state 结构；真实 GitHub 授权码回调由前端读 query 后 POST 到此
    ident = auth.exchange_github_code(req.code, req.state)
    token = auth.issue(ident["role"], ident["sub"], ident["name"], demo=False)
    r = JSONResponse(content={"ok": True, "logged_in": True, "role": ident["role"],
                              "name": ident["name"], "demo": False})
    _set_session_cookie(r, token)
    return r

@router.post("/api/auth/github/demo/confirm")
def auth_github_demo_confirm(req: AuthGithubDemoConfirmRequest):
    ident = auth.github_demo_confirm(req.state)
    token = auth.issue(ident["role"], ident["sub"], ident["name"], demo=True)
    r = JSONResponse(content={"ok": True, "logged_in": True, "role": ident["role"],
                              "name": ident["name"], "demo": True,
                              "notice": "GitHub 演示身份，不代表真实 GitHub 用户"})
    _set_session_cookie(r, token)
    return r

@router.post("/api/auth/logout")
def auth_logout(request: Request):
    auth.revoke(request.cookies.get(auth.COOKIE_NAME))
    r = JSONResponse(content={"ok": True, "logged_in": False})
    r.delete_cookie(auth.COOKIE_NAME, path="/")
    return r

@router.post("/api/auth/password/change")
def auth_password_change(req: AuthPasswordChangeRequest, request: Request):
    # 该端点在 /api/auth/ 下（公开前缀），须在端点内自行强制登录态
    ident = auth.identity(request.cookies.get(auth.COOKIE_NAME))
    if not ident:
        raise auth.AuthError("AUTH_REQUIRED", "请先登录后再修改密码", 401)
    result = local_accounts.change_password(ident["sub"], req.old_password, req.new_password)
    return {"ok": True, "changed_at": result["changed_at"]}

@router.post("/api/auth/password/login")
def auth_password_login(req: AuthPasswordLoginRequest):
    ident = local_accounts.login(req.identifier, req.password)
    token = auth.issue(ident["role"], ident["sub"], ident["name"], demo=False)
    r = JSONResponse(content={"ok": True, "logged_in": True, "role": ident["role"],
                              "name": ident["name"], "demo": False})
    _set_session_cookie(r, token)
    return r

@router.get("/api/auth/providers")
def auth_providers():
    """登录页据此渲染可用的登录方式；只返回形态与是否就绪，不含任何密钥。"""
    a = auth.status()
    return {
        "ok": True,
        "providers": {
            "local_accounts": a["local_accounts"],
            "wechat": {"mode": a["wechat"]["mode"],
                       "ready": a["wechat"]["real_oauth_ready"],
                       "demo_allowed": a["wechat"]["demo_allowed"]},
            "github": {"mode": a["github"]["mode"],
                       "ready": a["github"]["real_oauth_ready"],
                       "demo_allowed": a["github"]["demo_allowed"]},
            "admin": {"mode": "password", "demo": a["admin"]["demo_credential"]},
        },
        "page_login_required": a["page_login_required"],
    }

@router.post("/api/auth/register")
def auth_register(req: AuthRegisterRequest):
    ident = local_accounts.register(req.identifier, req.password, req.name)
    token = auth.issue(ident["role"], ident["sub"], ident["name"], demo=False)
    r = JSONResponse(content={"ok": True, "logged_in": True, "role": ident["role"],
                              "name": ident["name"], "demo": False,
                              "notice": "注册即登录；账号仅用于鉴权，不产出用户画像"})
    _set_session_cookie(r, token)
    return r

@router.get("/api/auth/session")
def auth_session(request: Request):
    ident = auth.identity(request.cookies.get(auth.COOKIE_NAME))
    page_required = secure_config.cfg.flag("AUTH_REQUIRE_LOGIN", True)
    if not ident:
        return {"ok": True, "logged_in": False, "role": None,
                "page_login_required": page_required}
    sub = ident.get("sub", "")
    return {
        "ok": True, "logged_in": True,
        "role": ident.get("role"), "name": ident.get("name", ""),
        "demo": bool(ident.get("demo")),
        "page_login_required": page_required,
        "has_local_password": sub.startswith("acct:"),
        "provider": ("local" if sub.startswith("acct:") else
                     "github" if sub.startswith("gh") else
                     "wechat" if sub.startswith("wx:") else
                     "admin" if ident.get("role") == auth.ROLE_ADMIN else "demo"),
    }

@router.get("/api/auth/status")
def auth_status():
    return auth.status()

@router.post("/api/auth/wechat/authorize")
def auth_wechat_authorize():
    wm = auth.wechat_mode()
    state = auth.new_state("real" if wm["real_oauth_ready"] else "demo")
    if wm["real_oauth_ready"]:
        return {"ok": True, "mode": "real", "authorize_url": auth.authorize_url(state), "state": state}
    if not wm["demo_allowed"]:
        return fail(403, "DEMO_DISABLED", "演示扫码已关闭，且未配置微信开放平台凭据")
    # 演示模式：无真实二维码，返回本地确认所需的 state，前端明确标注「演示」
    return {"ok": True, "mode": "demo", "authorize_url": None, "state": state,
            "demo_notice": "未配置微信开放平台凭据，当前为本地演示扫码，非真实微信登录"}

@router.post("/api/auth/wechat/callback")
def auth_wechat_callback(req: AuthWechatCallbackRequest):
    ident = auth.exchange_code(req.code, req.state)
    token = auth.issue(ident["role"], ident["sub"], ident["name"], demo=False)
    r = JSONResponse(content={"ok": True, "logged_in": True, "role": ident["role"],
                              "name": ident["name"], "demo": False})
    _set_session_cookie(r, token)
    return r

@router.post("/api/auth/wechat/demo/confirm")
def auth_wechat_demo_confirm(req: AuthDemoConfirmRequest):
    ident = auth.demo_confirm(req.state)
    token = auth.issue(ident["role"], ident["sub"], ident["name"], demo=True)
    r = JSONResponse(content={"ok": True, "logged_in": True, "role": ident["role"],
                              "name": ident["name"], "demo": True,
                              "notice": "演示身份，不代表真实微信用户"})
    _set_session_cookie(r, token)
    return r

@router.get("/api/github/repo")
def github_repo(owner: str = "", repo: str = ""):
    mod, err = _need("github_meta")
    if err:
        return err
    try:
        return mod.fetch(owner, repo)
    except mod.GithubBlocked as exc:
        return fail(403, "SOURCE_BLOCKED_OR_INVALID", str(exc)[:120])
    except mod.GithubUnavailable as exc:
        return fail(502, str(exc), "公开仓库元数据暂不可用；不影响其他功能")

@router.get("/api/github/status")
def github_status():
    mod, err = _need("github_meta")
    return err or {"ok": True, **mod.status()}
