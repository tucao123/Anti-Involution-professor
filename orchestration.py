#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""职引者 ZHIYIN · LangGraph 编排层（可选依赖 + 同语义标准库降级）

图定义（节点 → 边）：
    START → validate → extract → retrieve → [local | cloud] → finish → END
    retrieve 之后的分支是**合规分支**，不是性能分支：
      - local（默认）：C 级用户自带 JD 全程只在本机处理，产出确定性建议。
      - cloud：仅当调用方显式 use_cloud_llm=true 且 qwen_gate 放行时启用；
        只发送「技能名 / 级别 / 覆盖率」等结构化字段，**绝不发送 JD 原文、
        证据短句或简历原文**（与 /api/jd/analyze 的承诺一致）。
      - 云端节点失败自动回落 local，trace 中如实标注。

为什么要有 compat 引擎：
    langgraph 属于可选依赖，本机 / 离线演示 / CI 未必安装。这里保留完全相同的
    节点函数、边与条件跳转语义，用纯标准库执行，保证 7 个视图与 E2E 不中断。
    status() 会如实报告实际引擎，绝不把 compat 冒充成 langgraph。

原文处理：extract 节点之后立即从 state 中移除 jd_text / resume_text，
后续节点在结构上就拿不到用户原文，从代码层面杜绝误外发。
"""
from __future__ import annotations

import json
from typing import Any, Callable, TypedDict

import llm
import sources
import user_jd
import vector_store
from secure_config import qwen_gate


class GraphRejected(Exception):
    """授权 / 输入 / 来源校验未通过，code 见 str(exc)。"""


NODES = ("validate", "extract", "retrieve", "advise_local", "advise_cloud", "finish")
EDGES = (
    ("START", "validate"),
    ("validate", "extract"),
    ("extract", "retrieve"),
    ("retrieve", "advise_local | advise_cloud（条件分支）"),
    ("advise_local | advise_cloud", "finish"),
    ("finish", "END"),
)


# ---------------------------------------------------------------------------
# 节点函数：统一签名 (state) -> 增量 dict，两种引擎共用
# ---------------------------------------------------------------------------
def _n_validate(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("purpose_ack") is not True:
        raise GraphRejected("CONSENT_REQUIRED")
    jd = (state.get("jd_text") or "").strip()
    if not (user_jd.MIN_JD <= len(jd) <= user_jd.MAX_JD):
        raise GraphRejected("JD_LENGTH_INVALID")
    try:
        sources.assert_fetchable("user_jd")
    except sources.SourceBlocked:
        raise GraphRejected("SOURCE_NOT_APPROVED") from None
    return {"validated": True, "jd_length": len(jd)}


def _n_extract(state: dict[str, Any]) -> dict[str, Any]:
    result = user_jd.analyze(
        state["jd_text"], state.get("resume_text", ""),
        {"purpose_ack": True, "aggregate_opt_in": False},
    )
    skills = [{"name": s["name"], "level": s["level"], "hits": s.get("hits", 0)}
              for s in result["skills"]]
    # 结构化结果保留；原文与证据短句立即从 state 移除，后续节点无法接触
    return {
        "skills": skills,
        "groups": result["groups"],
        "alignment": result["alignment"],
        "title": result["title"],
        "session_id": result["session"]["id"],
        "pii_redacted": result["pii"]["redacted"],
        "grade": "C",
        "jd_text": "",
        "resume_text": "",
        "raw_removed": True,
    }


def _n_retrieve(state: dict[str, Any]) -> dict[str, Any]:
    """只查 D 级公开岗位样本作为技能词频参考，不作为市场结论。"""
    query = " ".join(s["name"] for s in state["skills"][:6]) or "技能"
    refs: list[dict[str, Any]] = []
    detail = "无可用参考样本"
    try:
        res = vector_store.search(query[:200], top_k=3, scope="public")
        refs = [{"title": h["title"], "score": h["score"], "grade": h["grade"],
                 "region": h["region"], "forbidden_claims": h["forbidden_claims"]}
                for h in res["hits"]]
        detail = res["backend"]
    except Exception:
        detail = "参考检索不可用，已跳过（不影响主流程）"
    cloud_ok = (state.get("use_cloud_llm") is True
                and qwen_gate()["can_call_chat"])
    return {"references": refs, "reference_backend": detail,
            "branch": "advise_cloud" if cloud_ok else "advise_local"}


def _local_advice(state: dict[str, Any]) -> list[str]:
    """确定性建议：只依据技能级别与覆盖率，不编造任何数值或市场结论。"""
    required = [s["name"] for s in state["skills"] if s["level"] == "required"]
    implicit = [s["name"] for s in state["skills"] if s["level"] == "implicit"]
    out: list[str] = []
    if required:
        out.append("把「%s」准备好可验证的项目证据：用一句背景、一句本人动作、一句量化结果改写简历。"
                   % "、".join(required[:3]))
    else:
        out.append("这份 JD 没有明确的必备技能词，建议先向招聘方确认核心职责，再决定准备顺序。")
    al = state.get("alignment")
    if al and isinstance(al, dict):
        out.append("对照覆盖率结果，优先补齐未覆盖的必备项；雷达分数是规则计算值，不是市场评分。")
    else:
        out.append("尚未填写简历要点，因此不计算覆盖率——补上技能与项目要点后可得到差距对照。")
    if implicit:
        out.append("「%s」属于隐含要求，面试中可用反问确认使用场景，不必先行堆砌工具清单。"
                   % "、".join(implicit[:3]))
    out.append("以上只针对这一份由你提供的 JD，用于个人准备；不构成行情、薪资或岗位推荐结论。")
    return out[:4]


def _n_advise_local(state: dict[str, Any]) -> dict[str, Any]:
    return {"advice": _local_advice(state), "advice_source": "deterministic-rules",
            "cloud_called": False}


def _n_advise_cloud(state: dict[str, Any]) -> dict[str, Any]:
    """只发送结构化字段；任何失败都回落本地规则，绝不把原文带进 payload。"""
    payload = {
        "skills": state["skills"],
        "groups": state["groups"],
        "alignment": state["alignment"],
        "target_role": state.get("title", ""),
        "reference_grade": "D",
        "boundary": "只做叙述与可行动建议，不得编造薪资、需求热度或市场数值",
    }
    try:
        raw = llm.generate("report", payload)
        data = llm.validate_output("report", raw)
        return {"advice": data["advice"][:3], "anchors": data["anchors"][:2],
                "advice_source": "qwen-structured-only", "cloud_called": True,
                "cloud_payload_scope": "skills/levels/alignment only"}
    except Exception:
        return {"advice": _local_advice(state), "advice_source": "cloud-failed->deterministic",
                "cloud_called": False, "fallback_reason": "qwen 不可用或输出未通过校验"}


def _n_finish(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "result": {
            "title": state.get("title", ""),
            "skills": state.get("skills", []),
            "groups": state.get("groups", {}),
            "alignment": state.get("alignment"),
            "advice": state.get("advice", []),
            "anchors": state.get("anchors", []),
            "references": state.get("references", []),
            "grade": "C",
            "applicability": "仅针对这一份用户提供的 JD；不代表中国境内招聘市场",
            "forbidden_claims": [
                "不得据此推断市场需求、薪资、同比或蓝海岗位",
                "D 级参考样本为全球远程岗，不代表国内市场",
            ],
        },
        "session_id": state.get("session_id"),
        "withdraw_url": "/api/jd/delete",
        "raw_text_persisted": False,
        "raw_text_sent_to_cloud": False,
    }


NODE_FNS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "validate": _n_validate,
    "extract": _n_extract,
    "retrieve": _n_retrieve,
    "advise_local": _n_advise_local,
    "advise_cloud": _n_advise_cloud,
    "finish": _n_finish,
}


class ZhiyinState(TypedDict, total=False):
    """LangGraph state schema。

    必须放在模块级：LangGraph 1.x 用 get_type_hints() 解析 channel 类型，
    函数内定义的 TypedDict + 局部别名注解会解析失败并静默退回 compat 引擎。
    字段名与 NODE_FNS 返回的增量 dict 完全一致，未登记的键会被丢弃。
    """
    purpose_ack: bool
    use_cloud_llm: bool
    jd_text: str
    resume_text: str
    branch: str
    ok: bool
    validated: bool
    jd_length: int
    skills: list[dict[str, Any]]
    groups: dict[str, Any]
    alignment: Any
    title: str
    session_id: Any
    pii_redacted: Any
    grade: str
    raw_removed: bool
    references: list[dict[str, Any]]
    reference_backend: str
    advice: list[str]
    advice_source: str
    cloud_called: bool
    anchors: list[Any]
    cloud_payload_scope: str
    fallback_reason: str
    result: dict[str, Any]
    withdraw_url: str
    raw_text_persisted: bool
    raw_text_sent_to_cloud: bool


# ---------------------------------------------------------------------------
# 引擎：真 LangGraph 优先，缺失时用同语义标准库执行器
# ---------------------------------------------------------------------------
_APP: Any = None


def _langgraph_app():
    """编译一次并缓存；失败不缓存，运行时装好依赖后下次调用可自动接管。"""
    global _APP
    if _APP is not None:
        return _APP
    try:
        from langgraph.graph import END, START, StateGraph  # type: ignore

        builder = StateGraph(ZhiyinState)
        for name, fn in NODE_FNS.items():
            builder.add_node(name, fn)
        builder.add_edge(START, "validate")
        builder.add_edge("validate", "extract")
        builder.add_edge("extract", "retrieve")
        builder.add_conditional_edges(
            "retrieve",
            lambda s: s.get("branch", "advise_local"),
            {"advise_local": "advise_local", "advise_cloud": "advise_cloud"},
        )
        builder.add_edge("advise_local", "finish")
        builder.add_edge("advise_cloud", "finish")
        builder.add_edge("finish", END)
        _APP = builder.compile()
        return _APP
    except Exception:
        return None


def langgraph_available() -> bool:
    try:
        import langgraph  # noqa: F401
        return True
    except Exception:
        return False


def _run_compat(state: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    trace: list[dict[str, Any]] = []
    for name in ("validate", "extract", "retrieve"):
        before_branch = state.get("branch")
        state.update(NODE_FNS[name](state))
        trace.append({"node": name, "branch": before_branch or "-"})
    branch = state.get("branch", "advise_local")
    state.update(NODE_FNS[branch](state))
    trace.append({"node": branch, "branch": branch})
    state.update(NODE_FNS["finish"](state))
    trace.append({"node": "finish", "branch": branch})
    return state, trace


def run(jd_text: str, resume_text: str = "", purpose_ack: bool = False,
        use_cloud_llm: bool = False) -> dict[str, Any]:
    state: dict[str, Any] = {
        "purpose_ack": purpose_ack, "use_cloud_llm": use_cloud_llm,
        "jd_text": jd_text, "resume_text": resume_text, "branch": "advise_local",
    }
    app = _langgraph_app()
    engine = "langgraph" if app is not None else "compat-stdlib"
    trace: list[dict[str, Any]] = []
    try:
        if app is not None:
            state = dict(app.invoke(state))
            for name in ("validate", "extract", "retrieve",
                         state.get("branch", "advise_local"), "finish"):
                trace.append({"node": name, "branch": state.get("branch", "-")})
        else:
            state, trace = _run_compat(state)
    except GraphRejected as exc:
        return {"ok": False, "error": str(exc), "engine": engine, "trace": trace}
    except user_jd.ConsentRequired:
        return {"ok": False, "error": "CONSENT_REQUIRED", "engine": engine, "trace": trace}
    except user_jd.InvalidInput as exc:
        return {"ok": False, "error": str(exc), "engine": engine, "trace": trace}
    except sources.SourceBlocked:
        return {"ok": False, "error": "SOURCE_NOT_APPROVED", "engine": engine, "trace": trace}
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__, "engine": engine, "trace": trace}

    out = dict(state.get("result", {}))
    out["ok"] = bool(state.get("ok", True))
    # 数据边界声明必须与结果一起经 HTTP 返回，供前端与回归用例核对
    out["raw_text_persisted"] = bool(state.get("raw_text_persisted", False))
    out["raw_text_sent_to_cloud"] = bool(state.get("raw_text_sent_to_cloud", False))
    # 撤回入口必须随结果一起返回，否则「可撤回删除」在编排链路上无法执行
    out["session_id"] = state.get("session_id")
    out["withdraw_url"] = state.get("withdraw_url") or "/api/jd/delete"
    out.update({
        "engine": engine,
        "trace": trace,
        "advice_source": state.get("advice_source", "deterministic-rules"),
        "cloud_called": state.get("cloud_called", False),
        "raw_text_removed_after_extract": state.get("raw_removed", False),
        "data_scope": "C 级用户自带 JD；原文零落盘、默认不出本机",
    })
    sources.log_assessment("graph_run", "user_jd",
                           {"engine": engine, "branch": state.get("branch"),
                            "cloud_called": state.get("cloud_called", False)})
    return out


def status() -> dict[str, Any]:
    return {
        "ok": True,
        "engine": "langgraph" if _langgraph_app() is not None else "compat-stdlib",
        "langgraph_installed": langgraph_available(),
        "nodes": list(NODES),
        "edges": [list(e) for e in EDGES],
        "conditional_branch": "retrieve → advise_local（默认） / advise_cloud（显式开启 + qwen 放行）",
        "cloud_llm_ready": qwen_gate()["can_call_chat"],
        "data_scope": "C 级原文在 extract 后即从 state 移除；云端节点只收结构化技能字段",
        "fallback": "云端节点失败自动回落确定性规则，trace 如实标注",
    }
