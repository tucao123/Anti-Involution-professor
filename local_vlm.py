#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""职引者 ZHIYIN · 本地多模态模型层（Ollama / Qwen3-VL-4B）

设计取舍（与 llm.py 云端通道严格分离）：
1. 端点固定为本机回环 127.0.0.1:11434，禁止指向任何远程地址——图片与简历原文不出机。
2. 因此这里**不做** llm.assert_outbound_safe 的「C 级原文禁止外发」拦截：本地推理不构成外发。
   但输出端复用同一套 fail-closed：privacy.scan_pii + job_info._PROFILING_WORDS + 字段白名单，
   模型幻觉出的姓名 / 手机 / 画像词一律整批拒绝，而不是尽力抹除。
3. 图片只在内存中转 base64，绝不落盘、不写 vault、不写 RAG、不写审计原文。
4. Ollama 未安装 / 模型未拉取 / 服务未启动时，status() 如实标注，所有方法抛 LocalVLMUnavailable，
   由调用方（server 端点）如实返回 503，不静默降级到正则，避免伪装成模型结果。
5. 模型名、端点、超时可从配置读取；固定 qwen3-vl:4b-instruct（Q4 约 3.3 GB，适配
   8 GB 显存）。必须用 Instruct 版：它支持关闭思考、输出稳定；qwen3-vl:4b（思考版）
   无关闭开关、易吃光输出预算导致解析失败，已从本机删除，不要配置回去。

双接口（LOCAL_VLM_API_STYLE 切换，默认 ollama）：
  - ollama（原生 qwen）：POST {root}/api/chat，think:false + /no_think + format:json。
    Qwen3  thinking 开关在原生端点才可靠，长文本也不会把预算全烧在思考上。
  - openai（兼容）：POST {root}/v1/chat/completions，response_format=json_object + chat_template_kwargs。
两条路径共用同一套 prompt / 结构化校验 / PII fail-closed，单次请求、失败如实报错，不做多请求重试。
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field, ValidationError, field_validator

import llm  # 复用结构化输出契约，避免重复造轮子
from secure_config import cfg, note_qwen_call

DEFAULT_BASE = "http://127.0.0.1:11434"          # 原生根地址（无 /v1）
# 固定用 Instruct 版：它支持关闭思考；qwen3-vl:4b（思考版）无关闭开关，思考会吃光
# 输出预算导致解析失败，已从本机删除，切勿把 LOCAL_VLM_MODEL 配回思考版。
DEFAULT_MODEL = "qwen3-vl:4b-instruct"
# Ollama 默认模型目录；可通过 OLLAMA_MODELS 环境变量覆盖，status() 会如实返回实际探测到的路径
DEFAULT_MODEL_DIR = Path.home() / ".ollama" / "models"


class LocalVLMUnavailable(Exception):
    """本地模型不可用；调用方应降级，不向用户回显细节。"""


def base_url() -> str:
    """原生根地址（无 /v1 后缀），两条接口都从这里派生。"""
    return (cfg.get("LOCAL_VLM_BASE_URL", DEFAULT_BASE).rstrip("/") or DEFAULT_BASE).removesuffix("/v1")


def api_style() -> str:
    """接口风格：ollama（原生 /api/chat，默认）或 openai（/v1/chat/completions）。"""
    v = cfg.get("LOCAL_VLM_API_STYLE", "ollama").strip().lower()
    return v if v in ("ollama", "openai") else "ollama"


def openai_url() -> str:
    return base_url() + "/v1"


def model_name() -> str:
    return cfg.get("LOCAL_VLM_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


def timeout_s() -> int:
    try:
        return max(5, min(300, int(cfg.get("LOCAL_VLM_TIMEOUT", "120"))))
    except ValueError:
        return 120


def token_budget() -> int:
    try:
        return max(512, min(8192, int(cfg.get("LOCAL_VLM_MAX_TOKENS", "4096"))))
    except ValueError:
        return 4096


def _host_ok(url: str) -> bool:
    """硬约束：只允许本机回环端点，杜绝把简历图片发往远程。兼容带 /v1 的旧配置。"""
    u = url.lower().removesuffix("/v1")
    return bool(re.match(r"^http://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$", u))


def model_dir() -> dict[str, Any]:
    """返回本机模型权重目录的探测结果（路径只用于告知用户，不含任何密钥）。
    优先取 OLLAMA_MODELS 环境变量，其次取配置 LOCAL_VLM_MODELS_DIR，最后才是默认目录。"""
    import os

    env = (os.environ.get("OLLAMA_MODELS") or "").strip()
    configured = cfg.get("LOCAL_VLM_MODELS_DIR", "").strip()
    if env:
        path, source = Path(env).expanduser(), "OLLAMA_MODELS 环境变量"
    elif configured:
        path, source = Path(configured).expanduser(), "LOCAL_VLM_MODELS_DIR 配置"
    else:
        path, source = DEFAULT_MODEL_DIR, "Ollama 默认目录"
    manifests = path / "manifests"
    blobs = path / "blobs"
    found = manifests.is_dir() or blobs.is_dir()
    return {
        "path": str(path),
        "source": source,
        "exists": path.is_dir(),
        "has_manifests": manifests.is_dir(),
        "has_blobs": blobs.is_dir(),
        "model_found": found,
    }


def _ollama_raw_tags() -> dict[str, Any] | None:
    """直连 Ollama 原生 /api/tags（比 OpenAI 兼容端点更适合探测与列模型）。"""
    base = base_url()
    if not _host_ok(base):
        return None
    root = base[:-3] if base.endswith("/v1") else base  # /v1 -> /
    req = urllib.request.Request(root + "/api/tags", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
            return data if isinstance(data, dict) else None
    except Exception:
        return None


def status() -> dict[str, Any]:
    """供 /api/local-vlm/status 使用；不含任何图像或文本内容。"""
    base = base_url()
    name = model_name()
    tags = _ollama_raw_tags()
    names: list[str] = []
    if tags and isinstance(tags.get("models"), list):
        names = [str(m.get("name") or "") for m in tags["models"] if isinstance(m, dict)]
    exact = any(n == name or n.startswith(name + ":") for n in names)
    # 模型 tag 可能带后缀（如 latest / q4_K_M）；按系列名兜底匹配，避免误判未拉取
    family = name.split(":")[0]
    family_hit = any(n.split(":")[0] == family for n in names)
    md = model_dir()
    style = api_style()
    endpoint = base + ("/api/chat" if style == "ollama" else "/v1/chat/completions")
    return {
        "ok": True,
        "provider": "ollama-local",
        "api_style": style,
        "endpoint": endpoint,
        "endpoint_loopback_only": _host_ok(base),
        "model": name,
        "server_reachable": tags is not None,
        "model_pulled": exact or family_hit,
        "models_on_server": names[:20],
        "model_dir": md,
        "data_leaves_device": False,
        "ready": bool(tags is not None and (exact or family_hit)),
        "reason": (
            "Ollama 服务不可达（未启动或未安装）" if tags is None else
            ("模型未拉取，请先执行 ollama pull " + name if not (exact or family_hit) else "本地视觉模型就绪")
        ),
    }


# ---------------------------------------------------------------------------
# 简历抽取契约：字段结构与 job_info.to_coarse_job_profile 对齐，但只由模型做语义识别，
# 公司名 / 学校名 / 个人信息 / 画像字段一律禁止输出，输出后仍过 fail-closed。
# ---------------------------------------------------------------------------
StrictModel = llm.StrictModel  # 单一契约来源

ShortField = Annotated[str, Field(max_length=80)]
CertList = Annotated[list[Annotated[str, Field(max_length=60)]], Field(max_length=20)]
# 项目/实习经历是给专家读的岗位背景，需要保留技术栈与职责概要，上限放宽到 120 字。
ExpList = Annotated[list[Annotated[str, Field(max_length=120)]], Field(max_length=12)]


def _as_list(v: Any, *, limit: int = 60, count: int = 20) -> list[str]:
    """模型偶尔把数组字段输出成中文顿号/逗号分隔的字符串，归一为列表（减法：适配而非较劲）。
    limit/count 可调：项目经历用更宽上限，证书/技能等短词保持默认。"""
    if isinstance(v, list):
        return [str(x)[:limit] for x in v if isinstance(x, (str, int, float))][:count]
    if isinstance(v, str) and v.strip():
        parts = re.split(r"[、,，;；\n]+", v)
        return [p.strip()[:limit] for p in parts if p.strip()][:count]
    return []


class ResumeExtract(StrictModel):
    target_role: ShortField = Field(default="", description="求职意向 / 目标岗位，无则空串")
    salary_expectation: ShortField = Field(default="", description="期望薪资，如 7000-9000元/月；无则空串")
    major: ShortField = Field(default="", description="专业")
    education: ShortField = Field(default="", description="学历层次，如本科 / 硕士")
    certificates: CertList = Field(default_factory=list, description="证书名称，不含发证机构")
    honors: CertList = Field(default_factory=list, description="荣誉奖项短名")
    skills: CertList = Field(default_factory=list, description="技能关键词短名，不含句子")
    experience_context: ExpList = Field(default_factory=list, description="项目或实习：每项含项目名+技术栈+做了什么，公司 / 学校匿名化为某单位 / 某学校")
    level_hint: Annotated[str, Field(pattern=r"^(junior|senior|unspecified)$")] = Field(
        default="unspecified", description="仅据岗位语境判断的级别")

    _norm = field_validator("certificates", "honors", "skills", mode="before")(
        lambda cls, v: _as_list(v))
    _norm_exp = field_validator("experience_context", mode="before")(
        lambda cls, v: _as_list(v, limit=120, count=12))


RESUME_EXTRACT_SYSTEM = """简历岗位字段抽取器。只输出一个JSON对象，字段名用英文，不要思考过程、不要markdown、不要解释。
禁止输出：姓名、手机号、邮箱、证件号、住址、性别、具体学校名、具体公司名；学校写「某学校」、公司写「某单位」。
禁止输出评分/画像词。skills 只写技能关键词。没有的字段留空字符串或空数组。
salary_expectation 填简历里的期望薪资原文（如「7000-9000元/月」），没有就留空串，不要估算。
education 只填学历层次（本科/硕士/博士/大专），若写成「计算机科学与技术（本科）」要拆出「本科」；不要把学校名当学历。
experience_context 每项写一个项目/实习：项目名 + 技术栈 + 做了什么（如「RAG答疑机器人：Python+LlamaIndex+重排，做文档入库与引用问答」），最多8项，每项60字内，保留每个项目，不要合并或只写极短短句。
level_hint 只能填 junior / senior / unspecified 三者之一（初级/应届填 junior，资深/高级填 senior，其余或不确定填 unspecified），不要填中文或其他值。"""


def _build_messages(task: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    # 只给紧凑字段名清单（不塞完整 JSON Schema：既省 token，也避免视觉模型照着中文简历结构乱命名字段）
    fields = "target_role, salary_expectation, major, education, certificates, honors, skills, experience_context, level_hint"
    if task == "resume_extract_image":
        # 图片视觉识别时模型容易把多个项目合并成一条；明确逐条列出，向文本路径看齐。
        text = ("从这张简历图片逐字识别并抽取岗位字段。只输出JSON对象，字段名用英文：" + fields +
                "\n逐条阅读图片里的每个项目/实习，experience_context 一项一个项目、不要合并，最多8项；"
                "education/薪资/技能/证书都要按图片原文抽，图片里没有的才留空。")
    else:
        text = ("从这段简历文字抽取岗位字段。只输出JSON对象，字段名用英文：" + fields +
                "\n\n简历文字：\n" + str(payload.get("resume_text") or "")[:4000])
    content: list[dict[str, Any]] = [{"type": "text", "text": text}]

    image_b64 = payload.get("image_base64")
    if task == "resume_extract_image":
        if not image_b64 or not isinstance(image_b64, str):
            raise LocalVLMUnavailable("IMAGE_MISSING")
        mime = str(payload.get("image_mime") or "image/jpeg")
        if mime not in ("image/jpeg", "image/png", "image/webp", "image/bmp"):
            raise LocalVLMUnavailable("IMAGE_MIME_UNSUPPORTED")
        content.append({"type": "image_url",
                        "image_url": {"url": "data:%s;base64,%s" % (mime, image_b64)}})
    return [
        {"role": "system", "content": RESUME_EXTRACT_SYSTEM},
        {"role": "user", "content": content if len(content) > 1 else text},
    ]


# 视觉模型常照着简历输出中文字段名 / 嵌套对象；统一归一到英文契约键。
_KEY_ALIAS = {
    "期望岗位": "target_role", "目标岗位": "target_role", "求职意向": "target_role",
    "专业": "major", "学历": "education",
    "证书": "certificates", "荣誉": "honors", "奖项": "honors",
    "技能": "skills", "专业技能": "skills", "技能方向": "skills",
    "项目经历": "experience_context", "实习经历": "experience_context", "经历": "experience_context",
    "级别": "level_hint",
}


def _normalize_keys(obj: dict[str, Any]) -> dict[str, Any]:
    """把模型输出的中文字段名 / 嵌套「教育背景」归一成英文契约键（减法：一次归一，契约不变）。"""
    out: dict[str, Any] = {}
    for k, v in obj.items():
        key = _KEY_ALIAS.get(str(k).strip(), str(k).strip())
        if k == "教育背景" and isinstance(v, dict):
            for kk, vv in v.items():
                merged = _KEY_ALIAS.get(str(kk).strip(), "")
                if "专业" in str(kk):
                    out["major"] = vv
                elif "学历" in str(kk):
                    out["education"] = vv
                elif merged:
                    out[merged] = vv
            continue
        if "专业" in str(k) and "学历" in str(v):  # 形如 {"专业/学历":"统计学·本科"}
            parts = re.split(r"[·/、]", str(v))
            out["major"] = parts[0].strip()
            if len(parts) > 1:
                out["education"] = parts[1].strip()
            continue
        out[key] = v
    # 薪资属用户自填的岗位约束字段（非画像/非预测），保留模型抽取结果。
    # 中文键归一到契约键；只保留简短薪资串，避免带出无关句子。
    if "期望薪资" in out:
        out["salary_expectation"] = out.pop("期望薪资")
    sal = str(out.get("salary_expectation") or "").strip()
    if sal:
        m = re.search(r"(\d[\d,\.]*\s*[-~—至到]\s*\d[\d,\.]*\s*(?:元|k|K|万)?(?:/月|每月|/月)?|\d[\d,\.]*\s*(?:元|k|K|万)\s*(?:以上|以下|起)?)", sal)
        out["salary_expectation"] = (m.group(1).strip() if m else sal[:30])
    # level_hint 归一到契约枚举（junior/senior/unspecified）。模型常输出中文级别词
    # 或空串，直接交给 Pydantic 会校验失败；这里先把弱信号字段归一，避免整批拒绝。
    _lh = str(out.get("level_hint") or "").strip().lower()
    if _lh in ("junior", "senior", "unspecified"):
        out["level_hint"] = _lh
    elif re.search(r"高级|资深|senior|专家|架构师|十年|8年|5年以上", _lh):
        out["level_hint"] = "senior"
    elif re.search(r"初级|应届|实习|junior|入门|1年|两年", _lh):
        out["level_hint"] = "junior"
    else:
        out["level_hint"] = "unspecified"
    # 字段串位纠正：major 里混进学历（如「统计学·本科」）时拆出学历补 education；
    # education 被填成「某学校 / 某单位」等非学历词时忽略。
    major = str(out.get("major") or "")
    if re.search(r"博士|硕士|研究生|本科|大专|专科|学士", major):
        m = re.search(r"(博士|硕士|研究生|本科|大专|专科|学士)", major)
        if m and not re.search(r"博士|硕士|研究生|本科|大专|专科|学士", str(out.get("education") or "")):
            out["education"] = m.group(1)
        out["major"] = re.sub(r"[·/（(].*$", "", major).strip() or major
    # 学历归一：模型常输出英文（bachelor/master）或噪声（某学校），统一到中文学历层次，
    # 避免前端学历字段显示英文/空值。顺序：英文学位 → 中文关键词 → 噪声清空。
    _edu = str(out.get("education") or "").strip()
    _edu_l = _edu.lower()
    _edu_map = [
        (r"博士|ph\.?d|doctor", "博士"),
        (r"硕士|master|研究生|m\.?s\.?c", "硕士"),
        (r"本科|bachelor|学士|undergraduate|^本科$", "本科"),
        (r"大专|专科|college|associate|职业技术学院", "大专"),
        (r"高中|中专|secondary", "高中"),
    ]
    _edu_hit = ""
    for _pat, _cn in _edu_map:
        if re.search(_pat, _edu_l) or re.search(_pat, _edu):
            _edu_hit = _cn
            break
    if _edu_hit:
        out["education"] = _edu_hit
    elif re.search(r"某学校|某单位|大学|学院|某大学", _edu) or not _edu:
        # 噪声或空：尝试从专业字段补学历，补不到就留空（由前端手写）
        if not re.search(r"博士|硕士|本科|大专|专科|研究生|学士", str(out.get("major") or "")):
            out["education"] = ""
    else:
        out["education"] = _edu[:40]
    # 字符串字段硬截断到契约上限 80，避免偶发超长整批校验失败（弱信号字段，截断无害）
    for _k in ("target_role", "major", "education"):
        if _k in out:
            out[_k] = str(out[_k])[:80] if out[_k] is not None else ""
    return out


def _build_request(style: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
    """按接口风格构造请求体。两条路径都关思考、都要 JSON，参数用各自端点认的写法。"""
    budget = token_budget()
    if style == "ollama":
        # 原生 /api/chat：think:false 是原生参数；/no_think 是 Qwen3 官方硬开关；
        # format:json 约束结构化输出；num_predict 给足预算避免思考吃光后 content 空。
        native = []
        for m in messages:
            item = {"role": m.get("role"), "content": ""}
            c = m.get("content")
            images: list[str] = []
            if isinstance(c, str):
                item["content"] = c
            elif isinstance(c, list):
                for part in c:
                    if part.get("type") == "text":
                        item["content"] += str(part.get("text") or "")
                    elif part.get("type") == "image_url":
                        url = str((part.get("image_url") or {}).get("url") or "")
                        if url.startswith("data:") and "," in url:
                            images.append(url.split(",", 1)[1])
            if images:
                item["images"] = images
            native.append(item)
        if native and native[0].get("role") == "system":
            native[0]["content"] = "/no_think\n" + str(native[0].get("content") or "")
        return {
            "model": model_name(),
            "messages": native,
            "stream": False,
            "think": False,
            "format": "json",
            "options": {"temperature": 0.1, "num_predict": budget},
        }
    # OpenAI 兼容 /v1/chat/completions：image_url data URL + response_format + chat_template_kwargs
    return {
        "model": model_name(),
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": budget,
        "stream": False,
        "think": False,
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {"type": "json_object"},
    }


def _repair_truncated_json(text: str) -> dict[str, Any] | None:
    """抢救被 num_predict 截断的半截 JSON：从首个 { 起扫描括号/引号，遇到不配对
    或字符串悬空即在此收尾，补全缺失的引号与闭合符再解析。仅在 strip_json 失败后
    调用，属于 fail-safe；能解析完整 JSON 时不会走到这里。"""
    start = text.find("{")
    if start < 0:
        return None
    s = text[start:]
    in_str = False
    esc = False
    stack: list[str] = []
    cutoff = len(s)
    for i, ch in enumerate(s):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            want = "{" if ch == "}" else "["
            if stack and stack[-1] == want:
                stack.pop()
            else:  # 遇到不配对的闭合符，说明后面是垃圾/解释文字，截断于此
                cutoff = i
                break
    s = s[:cutoff]
    if in_str:
        s += '"'  # 收尾：字符串没闭合
    # 截断可能停在逗号/冒号中间，去掉悬空分隔符
    s = s.rstrip()
    while s and s[-1] in ",:":
        s = s[:-1].rstrip()
    closes = "".join("}" if c == "{" else "]" for c in reversed(stack))
    try:
        obj = json.loads(s + closes)
        return obj if isinstance(obj, dict) else None
    except (ValueError, TypeError):
        return None


def _parse_json_candidates(style: str, result: dict[str, Any]) -> dict[str, Any] | None:
    """候选逐个试：先标准解析，再对损坏文本做配平修复，任一成功即返回。"""
    for candidate in _content_candidates(style, result):
        parsed = llm.strip_json(candidate)
        if parsed is not None:
            return parsed
        repaired = _repair_truncated_json(candidate)
        if repaired is not None:
            return repaired
    return None


def _content_candidates(style: str, result: dict[str, Any]) -> list[str]:
    """按优先级返回待解析文本候选， extract() 逐个试到能解析 JSON 为止。

    qwen3-vl 的 thinking 变体即使在 think:false 下也会生成思考。思考吃预算时
    content 常见三种坏形态：整段为空、半截 JSON（有 { 无 }）、夹带解释文字；
    原文捞不到时再从 thinking 里捞一次。这里只负责给候选，能否解析由 strip_json 判定，
    不在本函数里提前 strip，避免「content 非空但已损坏」被误判为无需兜底。"""
    try:
        if style == "ollama":
            msg = result["message"]
            candidates: list[str] = []
            content = str(msg.get("content") or "")
            if content.strip():
                candidates.append(content)
            think = str(msg.get("thinking") or "")
            if think and "{" in think:
                candidates.append(think)
            return candidates or [""]
        return [str(result["choices"][0]["message"]["content"])]
    except (KeyError, IndexError, TypeError):
        raise LocalVLMUnavailable("BAD_RESPONSE") from None


def extract(task: str, payload: dict[str, Any]) -> dict[str, Any]:
    """调用本机 VLM 抽取岗位字段；返回已过 Schema 校验的 dict。失败抛 LocalVLMUnavailable。"""
    if task not in ("resume_extract_image", "resume_extract_text"):
        raise LocalVLMUnavailable("UNKNOWN_TASK")
    base = base_url()
    if not _host_ok(base):
        raise LocalVLMUnavailable("ENDPOINT_NOT_LOOPBACK")
    # 外发前置检查不适用（数据不出机）；但仍拒绝明显不可序列化的对象
    try:
        json.dumps(payload, ensure_ascii=False)
    except (TypeError, ValueError):
        raise LocalVLMUnavailable("PAYLOAD_NOT_SERIALIZABLE") from None

    messages = _build_messages(task, payload)
    style = api_style()
    url = base + "/api/chat" if style == "ollama" else base + "/v1/chat/completions"
    body = _build_request(style, messages)

    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout_s()) as resp:
            result = json.loads(resp.read().decode("utf-8", errors="ignore"))
        parsed = _parse_json_candidates(style, result)
        if parsed is None:
            raise LocalVLMUnavailable("NOT_JSON")
        data = ResumeExtract.model_validate(_normalize_keys(parsed)).model_dump()
        note_qwen_call(True)
        return data
    except ValidationError:
        note_qwen_call(False)
        raise LocalVLMUnavailable("SCHEMA_VALIDATION_FAILED") from None
    except LocalVLMUnavailable:
        note_qwen_call(False)
        raise
    except (urllib.error.URLError, ConnectionResetError, BrokenPipeError):
        note_qwen_call(False)
        raise LocalVLMUnavailable("UNREACHABLE") from None
    # 超时不是 URLError 子类：单独归类，避免掉进笼统的 LOCAL_VLM_ERROR
    except TimeoutError:
        note_qwen_call(False)
        raise LocalVLMUnavailable("TIMEOUT") from None
    except Exception as exc:
        note_qwen_call(False)
        # 保留真实异常类型到 stderr，避免兜底码让问题无法诊断
        import sys
        print("[local_vlm] LOCAL_VLM_ERROR %s: %s" %
              (type(exc).__name__, str(exc)[:200]), file=sys.stderr, flush=True)
        raise LocalVLMUnavailable("LOCAL_VLM_ERROR") from None
