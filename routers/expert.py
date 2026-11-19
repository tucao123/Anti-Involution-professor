#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""路由域：专家链路（LLM/辅导/混合检索/洞察）（拆分自 server.py，行为保持一致）。"""
from __future__ import annotations

from fastapi import APIRouter
from .common import *  # noqa: F401,F403
from .common import (_optional, _metrics, _set_session_cookie, _require_identity,
                     _privacy_identity, _privacy_owns, _need)  # noqa: F401

router = APIRouter()

@router.post("/api/coach/chat")
def coach_chat(req: CoachChatRequest):
    if not req.purpose_ack:
        return fail(403, "CONSENT_REQUIRED", "请先确认用途授权；默认不视为同意")
    mod, err = _need("coach")
    if err:
        return err
    try:
        return mod.chat(req.session_id, req.message, req.purpose_ack)
    except mod.SessionNotFound:
        return fail(404, "SESSION_EXPIRED_OR_UNKNOWN", "会话已过期或已删除，请重新开始")
    except mod.ConsentRequired:
        return fail(403, "CONSENT_REQUIRED")
    except ValueError as exc:
        return fail(400, type(exc).__name__, str(exc)[:80])
    except Exception as exc:
        return fail(400, type(exc).__name__, "辅导请求未处理，输入未保存")

@router.post("/api/coach/delete")
def coach_delete(req: CoachDeleteRequest):
    mod, err = _need("coach")
    if err:
        return err
    removed = mod.delete(req.session_id)
    privacy.audit("coach_session_deleted", "", "removed=%s" % removed)
    return {"ok": True, "removed": removed, "residual": "none (in-memory only)"}

@router.get("/api/coach/status")
def coach_status():
    mod, err = _need("coach")
    return err or {"ok": True, **mod.status()}

@router.post("/api/expert/context")
def expert_context(req: ExpertContextRequest, request: Request):
    """所有专家 agent 的统一 RAG 证据入口（登录用户）。

    - agent=insight / coach：岗位专家，检索岗位信息索引；
    - agent=interview：面试专家，岗位信息索引 + 面试话术索引双路各自召回重排。

    岗位字段与用户问题只作检索条件，不写库、不落盘、不进用户画像。
    检索失败只返回空证据，不阻断专家生成（与洞察端点同策略）。
    """
    ident = auth.identity(request.cookies.get(auth.COOKIE_NAME))
    if not ident:
        return fail(401, "LOGIN_REQUIRED", "请先登录后再使用专家解读")
    mod, err = _need("hybrid")
    if err:
        return err
    payload = {
        "target_role": req.target_role, "major": req.major, "skills": req.skills,
        "experience": req.experience, "jds": req.jds,
    }
    try:
        if req.agent == "interview":
            result = mod.for_interview(payload, top_k=req.top_k,
                                       focus=req.focus, stage=req.stage)
            scene = "interview"
        else:
            result = mod.for_expert(payload, top_k=req.top_k, focus=req.focus,
                                    scene=req.agent)
            scene = req.agent
    except Exception:
        result = {"ok": True, "materials": [], "count": 0,
                  "trace": {"scene": req.agent, "retrieval": "检索暂不可用", "rerank": "未执行",
                            "data_policy": mod.DATA_POLICY_NOTE},
                  "disclaimer": "检索暂不可用；用户字段未入库未落盘。"}
    privacy.audit("expert_context", detail="场景 %s · 证据 %d 条" % (
        scene, result.get("count", 0)))
    return result

@router.post("/api/hybrid/compare")
def hybrid_compare(req: HybridCompareRequest):
    mod, err = _need("hybrid")
    if err:
        return err
    try:
        return mod.compare(req.q, req.top_k)
    except mod.InvalidQuery as exc:
        return fail(400, str(exc), "检索词过短，已拒绝")

@router.post("/api/hybrid/search")
def hybrid_search(req: HybridSearchRequest):
    mod, err = _need("hybrid")
    if err:
        return err
    try:
        return mod.search(req.q, req.top_k, req.include_user_local, req.use_dense)
    except mod.InvalidQuery as exc:
        return fail(400, str(exc), "检索词过短，已拒绝")
    except Exception as exc:
        return fail(400, type(exc).__name__, "混合检索失败，已保留输入不落盘")

@router.get("/api/hybrid/status")
def hybrid_status():
    mod, err = _need("hybrid")
    return err or {"ok": True, **mod.status()}

@router.post("/api/insight/context")
def insight_context(req: InsightContextRequest, request: Request):
    """洞察 RAG：岗位字段 → 检索意图 → 双路召回 → 精排 → D 级脱敏证据。
    仅本次请求内存处理；字段是查询条件，不写库、不落盘、不进用户画像。"""
    ident = auth.identity(request.cookies.get(auth.COOKIE_NAME))
    if not ident:
        return fail(401, "LOGIN_REQUIRED", "请先登录后再生成洞察")
    mod, err = _need("hybrid")
    if err:
        return err
    payload = {
        "target_role": req.target_role, "major": req.major, "skills": req.skills,
        "experience": req.experience, "jds": req.jds,
    }
    try:
        result = mod.for_insight(payload, top_k=req.top_k)
    except Exception:
        # 检索失败不阻断洞察：返回空证据，前端仍可用静态统计生成
        result = {"ok": True, "materials": [], "count": 0,
                  "trace": {"retrieval": "检索暂不可用", "rerank": "未执行",
                            "data_policy": mod.DATA_POLICY_NOTE},
                  "disclaimer": "检索暂不可用，改用样本统计；用户字段未入库未落盘。"}
    privacy.audit("insight_context", detail="意图 %d 条 · 证据 %d 条" % (
        len(result.get("trace", {}).get("queries", [])), result.get("count", 0)))
    return result

@router.get("/api/insight/modes")
def insight_modes():
    return sources.insight_modes()

@router.post("/api/llm")
def llm_route(req: LLMRequest, request: Request):
    found = privacy.scan_pii(req.payload)
    if found:
        privacy.audit("llm_pii_blocked", detail="拦截字段：" + ",".join(found))
        return fail(403, "PII_BLOCKED", "检测到个人信息，按合规策略不外发至云端模型；本地模型路由尚未配置")
    # 登录用户优先用个人空间自带 API；否则回落到服务端 qwen（可选）。
    ident = auth.identity(request.cookies.get(auth.COOKIE_NAME))
    conn = user_llm.get(ident["sub"]) if ident else None
    if conn:
        try:
            user_llm.validate_endpoint(conn.get("base_url", ""))
        except user_llm.UserLLMError:
            conn = None  # 端点被新规则否定，安全回落到服务端链路
    try:
        raw = llm.generate(req.task, req.payload, connection=conn)
    except llm.LLMUnavailable as exc:
        code = str(exc)
        if code in ("PII_BLOCKED", "USER_DATA_BLOCKED"):
            privacy.audit("llm_user_data_blocked", detail=code)
            return fail(403, code, "C 级用户自带数据按默认策略不出本机")
        if conn and code == "USER_LLM_CONFIG_INVALID":
            return fail(400, code, "个人 API 配置不合法，请到个人空间检查")
        if not conn and not llm.qwen_gate()["can_call_chat"]:
            return fail(503, "LLM_NOT_ENABLED",
                        "未配置个人 API 且服务端 qwen 未启用；已自动降级到离线规则引擎")
        return fail(502, code, "模型调用未成功，已切换到离线演示模式")
    try:
        data = llm.validate_output(req.task, raw)
    except ValidationError:
        return fail(502, "SCHEMA_VALIDATION_FAILED", "模型输出未通过结构化校验，已切换到离线演示模式")
    return {"ok": True,
            "provider": (conn.get("provider") if conn else "qwen"),
            "credential_scope": "user" if conn else "server",
            "model": (conn.get("model") if conn else llm.status()["model"]),
            "validated": True, "data": data}

@router.get("/api/llm/status")
def llm_status():
    return {"ok": True, **llm.status()}

@router.post("/api/me/llm/delete")
def my_llm_delete(request: Request):
    ident = _require_identity(request)
    user_llm.delete(ident["sub"])
    return {"ok": True, "configured": False}

@router.get("/api/me/llm/providers")
def my_llm_providers():
    return user_llm.providers_view()

@router.post("/api/me/llm/save")
def my_llm_save(req: UserLLMConfigRequest, request: Request):
    ident = _require_identity(request)
    try:
        return user_llm.save(
            ident["sub"], provider=req.provider, api_key=req.api_key,
            base_url=req.base_url, model=req.model, timeout=req.timeout)
    except user_llm.UserLLMError as exc:
        return fail(exc.status, exc.code, exc.message)

@router.get("/api/me/llm")
def my_llm_status(request: Request):
    ident = _require_identity(request)
    return user_llm.public_view(ident["sub"])

@router.post("/api/me/llm/test")
def my_llm_test(request: Request):
    """用本人配置跑一次最小结构化生成；只回成功与否，不回显上游响应。"""
    ident = _require_identity(request)
    conn = user_llm.get(ident["sub"])
    if not conn:
        return fail(404, "USER_LLM_NOT_CONFIGURED", "尚未配置个人大模型 API")
    try:
        llm.generate("interview_question",
                     {"role": "测试岗位", "stage": "自我介绍", "question": "请用一句话自我介绍。"},
                     connection=conn)
    except llm.LLMUnavailable as exc:
        return fail(502, str(exc), "个人 API 调用未成功；密钥不会被回显")
    except Exception:
        return fail(502, "USER_LLM_TEST_FAILED", "个人 API 调用未成功；密钥不会被回显")
    return {"ok": True, "tested": True}
