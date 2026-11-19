#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""路由域：JTBD 决策工作台（拆分自 server.py，行为保持一致）。"""
from __future__ import annotations

from fastapi import APIRouter
from .common import *  # noqa: F401,F403
from .common import (_optional, _metrics, _set_session_cookie, _require_identity,
                     _privacy_identity, _privacy_owns, _need)  # noqa: F401

router = APIRouter()

@router.post("/api/jtbd/actions")
def jtbd_actions(req: JtbdActionsRequest):
    if not isinstance(req.evidence, dict) or "requirements" not in req.evidence:
        return fail(400, "INVALID_EVIDENCE", "请先完成一次要求—证据对照")
    return jtbd.actions(req.evidence, req.override)

@router.post("/api/jtbd/compare")
def jtbd_compare(req: JtbdCompareRequest):
    try:
        result = jtbd.compare([i.model_dump() for i in req.jds], req.resume_text)
    except jtbd.InvalidInput as exc:
        return fail(400, "INVALID_INPUT", str(exc))
    sources.log_assessment("jtbd_compare", "user_jd",
                           {"grade": "C", "samples": result["sample_size"]})
    return result

@router.post("/api/jtbd/diagnose")
def jtbd_diagnose(req: JtbdDiagnoseRequest):
    if not isinstance(req.evidence, dict) or "requirements" not in req.evidence:
        return fail(400, "INVALID_EVIDENCE", "请先完成一次要求—证据对照")
    return jtbd.diagnose(req.evidence, req.resume_text)

@router.post("/api/jtbd/drill")
def jtbd_drill(req: JtbdDrillRequest):
    try:
        return jtbd.drill(req.requirement, req.project_text, req.depth)
    except jtbd.InvalidInput as exc:
        return fail(400, "INVALID_INPUT", str(exc))

@router.post("/api/jtbd/evidence")
def jtbd_evidence(req: JtbdEvidenceRequest):
    try:
        result = jtbd.evidence(req.jd_text, req.resume_text)
    except jtbd.InvalidInput as exc:
        return fail(400, "INVALID_INPUT", str(exc))
    sources.log_assessment("jtbd_evidence", "user_jd",
                           {"grade": "C", "requirements": result["total"]})
    return result

@router.post("/api/jtbd/review")
def jtbd_review(req: JtbdReviewRequest):
    return jtbd.review_answer(req.question, req.answer)

@router.get("/api/jtbd/status")
def jtbd_status():
    return jtbd.status()
