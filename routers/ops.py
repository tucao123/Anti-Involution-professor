#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""路由域：运维 / 状态 / 编排（拆分自 server.py，行为保持一致）。"""
from __future__ import annotations

from fastapi import APIRouter
from .common import *  # noqa: F401,F403
from .common import (_optional, _metrics, _set_session_cookie, _require_identity,
                     _privacy_identity, _privacy_owns, _need)  # noqa: F401

router = APIRouter()

@router.get("/api/approvals/status")
def approvals_status():
    """一次查看 5 项回执与双闸门状态；只读，不提供自我授权入口。"""
    return approval_gate.state()

@router.get("/api/capture/policy")
def capture_policy():
    """只读：截图输入策略（用户/管理员手动截图 + 浏览器本地 OCR）。不执行任何采集。"""
    return sources.capture_policy()

@router.get("/api/config")
def config_view():
    """配置状态视图：只有是否配置 / 来源类别 / 形态，绝不含任何密钥值。"""
    view = secure_config.public_view()
    view["credential_boundary"] = {
        "llm": "用户个人空间自带 API（加密存储，按登录身份隔离）；服务端 qwen 仅可选兜底",
        "embedding": "服务器固定本地 BGE 512 维，用户不可选择 / 覆盖",
        "user_llm_configurable": True,
        "embedding_user_selectable": False,
    }
    view["user_llm_providers"] = list(user_llm.PROVIDERS)
    return view

@router.post("/api/graph/run")
def graph_run(req: GraphRunRequest):
    if not req.purpose_ack:
        return fail(403, "CONSENT_REQUIRED", "请先确认用途授权；默认不视为同意")
    mod, err = _need("orchestration")
    if err:
        return err
    try:
        return mod.run(jd_text=req.jd_text, resume_text=req.resume_text,
                       purpose_ack=req.purpose_ack, use_cloud_llm=req.use_cloud_llm)
    except Exception as exc:
        return fail(400, type(exc).__name__, "编排执行失败，已保留输入不落盘")

@router.get("/api/graph/status")
def graph_status():
    mod, err = _need("orchestration")
    return err or {"ok": True, **mod.status()}

@router.get("/api/health")
def health():
    ls = llm.status()
    es = embedding.status()
    return {
        "ok": True,
        "phase": 2,
        "provider": "qwen",
        "model": ls["model"],
        "configured": ls["configured"],     # 服务端兜底是否就绪；用户自带 API 另按会话判定
        "mode": ls["mode"],
        "credential_source": ls["credential_source"],
        "llm_credential_scope": "user_configurable_or_server_fallback",
        "user_llm_configurable": True,
        "embedding_scope": "server_fixed_local_bge",
        "embedding_user_selectable": False,
        "framework": "fastapi",
        "schema_validation": True,
        "source_gate": "fail-closed",
        "market_scope": "global_remote / grade D（不代表中国境内招聘市场）",
        "insight_modes": ["skill_reference (ready)", "cn_market (pending_license)"],
        "user_jd_scope": "user_provided / grade C（原文零落盘、可撤回、默认不出本机）",
        "capture_policy": "automated_capture=removed / admin_text_or_screenshot_ocr=allowed / "
                          "pii_redact+job_only / rag_ingest=allowed(qdrant_cloud)",
        "research_mode": "admin_curated_only / no_third_party_capture",
        "rag_scope": "research-only / text-only / PII-redacted / job-info-only / qdrant-cloud / deletable",
        "components": {
            "llm": "qwen/" + ls["mode"],
            "embedding": es["selected_backend"]["public"],
            "qdrant": "configured" if secure_config.secret("QDRANT_URL") else "not-configured",
            "relational": "adapter+sqlite-fallback",
            "cache": "adapter+memory-fallback",
            "graph": "adapter+local-fallback",
            "orchestration": "langgraph-or-compat",
            "celery": "eager-fallback",
            "frontend": "workbench.html (JTBD) + index.html (legacy) + web/ (Next.js)",
        },
        "cloud_user_data_allowed": secure_config.cfg.flag("ALLOW_CLOUD_FOR_USER_DATA"),
    }

@router.get("/api/market")
def market_insight(major: str = "计算机科学与技术"):
    major = (major or "").strip()[:40] or "计算机科学与技术"
    return {"ok": True, **market.get_market_profile(major)}

@router.get("/metrics", include_in_schema=False)
def metrics_endpoint():
    mod, err = _need("metrics")
    if err:
        return PlainTextResponse("# metrics module not ready\n", status_code=503)
    return PlainTextResponse(mod.render(), media_type="text/plain; version=0.0.4; charset=utf-8")

@router.get("/api/phase3/status")
def phase3_status():
    """一次查看 A1/A2/A3/A4 的实际状态，避免把「已接适配器」误读为「已启用」。"""
    coach, hybrid_m, gh = _optional("coach"), _optional("hybrid"), _optional("github_meta")
    return {
        "ok": True,
        "A1_coach": {"ready": coach is not None, **(coach.status() if coach else {})},
        "A2_voice": {"ready": True, "engine": "browser-side Web Speech",
                     "server_audio_upload": False, "audio_may_leave_device": True,
                     "upload_endpoint": None},
        "A3_github": {"ready": gh is not None, **(gh.status() if gh else {})},
        "A4_hybrid": {"ready": hybrid_m is not None, **(hybrid_m.status() if hybrid_m else {})},
    }

@router.get("/api/project/usage")
def project_usage_policy():
    """只读：返回「仅限研究学习 / 禁止商业行为」成文条款与逐项适用说明。"""
    return {
        "ok": True,
        "policy": usage_policy.as_dict(),
        "applies_to_items": {k: usage_policy.applies_to(k) for k in approval_gate.ITEMS},
        "gate_state_changed": False,
        "note": "该条款对 5 项待决事项同理适用（收紧用途），但不改变任何 decision 或闸门状态；"
                "不替代法务书面结论、站点许可与商务合同。",
        "disclaimer": usage_policy.POLICY["disclaimer"],
    }

@router.get("/api/research/policy")
def research_policy():
    """只读：自动采集已移除，知识来源只剩管理员文本 / 截图 OCR。"""
    return {
        "ok": True,
        "verdict": "admin_curated_only",
        "automated_capture_executor": "removed",
        "production_capture_unlocked": False,
        "third_party_requests": 0,
        "admin_source": knowledge_ingest.status(),
        "principle": "系统不访问任何第三方页面；知识由管理员主动提供文本或截图（浏览器本地 OCR），"
                     "经脱敏与仅岗位信息过滤后入库。",
        "note": "原受控研究采集执行体已整体删除；本端点只声明当前策略，不执行采集。",
    }

@router.get("/api/slo", include_in_schema=False)
def slo_endpoint():
    mod, err = _need("metrics")
    if err:
        return fail(503, "MODULE_NOT_READY")
    return {"ok": True, **mod.slo_view()}

@router.get("/api/sources")
def source_registry():
    return sources.registry_view()

@router.get("/api/stores/status")
def stores_status():
    mod, err = _need("stores")
    return err or {"ok": True, **mod.status()}

@router.post("/api/tasks/dispatch")
def tasks_dispatch(req: TaskDispatchRequest):
    mod, err = _need("celery_app")
    if err:
        return err
    try:
        return mod.dispatch(req.task_name, req.kwargs)
    except Exception as exc:
        return fail(400, type(exc).__name__, "任务派发失败")

@router.get("/api/tasks/status")
def tasks_status():
    mod, err = _need("celery_app")
    return err or {"ok": True, **mod.status()}

@router.get("/api/voice/policy")
def voice_policy():
    """A2：Web Speech 的真实边界。服务端不接收音频，但不虚报「音频不出设备」。"""
    return {
        "ok": True,
        "engine": "Web Speech API (browser-side)",
        "asr": "SpeechRecognition / webkitSpeechRecognition",
        "tts": "speechSynthesis",
        "server_audio_upload": False,
        "server_upload_endpoint": None,
        "audio_may_leave_device": True,
        "browser_asr_note": (
            "SpeechRecognition 在 Chrome 等浏览器由浏览器厂商的云端服务完成识别，"
            "麦克风音频可能离开设备；本项目只承诺『本地 BFF 不接收、不保存任何音频』，"
            "不承诺『音频完全不出设备』。需要完全离线 ASR 时应改用 WASM / 本地模型（尚未实现）。"
        ),
        "microphone_permission": "不自动启动；用户每次显式点击并逐次授权，拒绝后降级为文字输入",
        "fallback": "不支持或拒绝权限时禁用语音按钮并保留完整文字流程",
        "note": "识别结果只写入当前页面的输入框，不落盘、不回传服务端（除非用户主动发送文字）。",
    }
