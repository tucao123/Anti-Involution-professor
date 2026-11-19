#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回归：简历解析读取岗位颗粒度字段（求职岗位 / 专业 / 证书），
但姓名 / 手机 / 邮箱 / 证件号等个人信息一律剔除；前端展示不暴露 JSON schema。"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import job_info  # noqa: E402

ok = 0
bad = 0


def check(cond, msg, extra=""):
    global ok, bad
    if cond:
        ok += 1
        print("  PASS", msg)
    else:
        bad += 1
        print("  FAIL", msg, extra)


RESUME = "\n".join([
    "姓名：张三",
    "手机：13800138000",
    "邮箱：zhangsan@example.com",
    "身份证：110101199001011234",
    "出生地：上海市",
    "性别：女",
    "毕业院校：某某大学",
    "求职意向：数据分析师",
    "薪资意向：10k-13k",
    "专业：统计学",
    "学历：硕士",
    "证书：英语四级、ACP、华为高级应用开发者证书、大学英语四级证书、荣誉",
    "荣誉：校级一等奖学金",
    "项目经历：",
    "项目名称：用户行为分析看板",
    "负责数据清洗、SQL建模与可视化，产出3张分析图表，帮助运营定位活跃下降原因",
    "实习经历：",
    "在华为技术有限公司参与需求分析并与产品同事对齐指标口径，用Python脚本整理周报数据",
    "岗位职责：熟悉 Python 与 SQL，负责数据清洗与统计分析",
])

print("=== 岗位颗粒度字段读取 ===")
r = job_info.to_coarse_job_profile(RESUME)
check(r.get("target_role") == "数据分析师", "求职意向已读取", r.get("target_role"))
check(r.get("salary_expectation") == "10k-13k", "薪资意向已读取", r.get("salary_expectation"))
check(r.get("major") == "统计学", "专业已读取", r.get("major"))
check(r.get("education") == "硕士", "学历已读取", r.get("education"))
certs = r.get("certificates", [])
check("大学英语四级（CET-4）" in certs, "英语四级已归一", certs)
check("ACP 认证" in certs, "ACP 已读取", certs)
check("华为高级应用开发者认证" in certs, "华为认证已读取", certs)
check("荣誉" not in certs and len(certs) == 3, "证书去重且荣誉标签未混入", certs)
check(any("一等奖学金" in h for h in r.get("honors", [])), "荣誉内容已读取", r.get("honors"))
check(r.get("job_title_hint") == "数据分析师", "岗位提示取求职意向", r.get("job_title_hint"))
check(r.get("level_hint") == "junior", "实习经历标注为初级/应届，证书高级不误判", r.get("level_hint"))
check(any(s["name"] == "Python" for s in r.get("skill_areas", [])), "技能方向仍保留")
ctx = r.get("expert_context", "")
check("求职意向：数据分析师" in ctx and "薪资意向（用户自填）：10k-13k" in ctx,
      "专家上下文含求职意向与薪资意向", ctx)
check(ctx.count("大学英语四级（CET-4）") == 1, "专家上下文证书不重复", ctx)
exp = r.get("experience_context", [])
check(any("用户行为分析看板" in x for x in exp), "项目上下文已提取", exp)
check(any("SQL建模" in x and "运营定位" in x for x in exp), "项目动作与结果已提取", exp)
check(any("对齐指标口径" in x for x in exp), "实习关键上下文已提取", exp)
check("项目/实习上下文" in ctx, "专家上下文包含项目/实习内容", ctx)

print("=== 项目/实习标题与内容同行写法 ===")
INLINE = "\n".join([
    "求职意向：数据分析师",
    "专业：统计学",
    "项目经验：参与用户行为分析项目，负责数据清洗、SQL建模与可视化，产出3张报表",
    "实习经验：在某公司做数据分析实习，用Python处理周报数据",
])
r2 = job_info.to_coarse_job_profile(INLINE)
check(r2.get("target_role") == "数据分析师", "同行写法求职意向已读取", r2.get("target_role"))
exp2 = r2.get("experience_context", [])
check(any("用户行为分析" in x and "数据清洗" in x for x in exp2), "同行项目经历已提取", exp2)
check(any("数据分析实习" in x for x in exp2), "同行实习经历已提取", exp2)

print("=== 同行身份字段 + 无标签教育行 ===")
MIXED = "\n".join([
    "姓名：张三 手机：13800138000 求职意向：AI Agent 开发工程师",
    "邮箱：zhangsan@example.com 薪资意向：7000-9000元/月",
    "教育经历：",
    "某某理工大学 计算机科学与技术（本科） 2023年9月-2027年6月",
    "项目经验：负责数据清洗、文本切片与向量化，用 Python + BGE + Qdrant，准确率从48%提升到100%",
    "实习实践：参与需求对齐，用 Python 脚本整理周报数据",
    "证书：ACP 认证",
])
r3 = job_info.to_coarse_job_profile(MIXED)
check(r3.get("target_role") == "AI Agent 开发工程师",
      "同行身份字段被删但求职意向保留", r3.get("target_role"))
check(r3.get("major") == "计算机科学与技术", "无标签教育行推断专业", r3.get("major"))
check(r3.get("education") == "本科", "无标签教育行推断学历", r3.get("education"))
check(any("向量化" in x and "Qdrant" in x for x in r3.get("experience_context", [])),
      "扩展标题「项目经验」同行内容已提取", r3.get("experience_context"))
check(any("需求对齐" in x for x in r3.get("experience_context", [])),
      "扩展标题「实习实践」已提取", r3.get("experience_context"))
check(all(t not in str(r3) for t in ("张三", "13800138000", "zhangsan@example.com", "某某理工大学")),
      "同行身份字段与学校名仍在输出中剔除")

print("=== 非标准经历标题兜底 ===")
ODD = "\n".join([
    "求职意向：AI Agent 开发工程师",
    "代表性项目 / 实习",
    "项目名称：WEATHER Agent 智能问答",
    "主导数据清洗、文本切片与向量化，使用 Python + BGE + Qdrant，把检索准确率从48%提升到100%",
    "实习内容：参与需求对齐与指标口径梳理，用 Python 脚本整理周报数据",
])
r4 = job_info.to_coarse_job_profile(ODD)
check(any("WEATHER Agent" in x for x in r4.get("experience_context", [])),
      "非标准标题下项目名已提取", r4.get("experience_context"))
check(any("向量化" in x and "Qdrant" in x for x in r4.get("experience_context", [])),
      "非标准标题下项目动作已兜底提取", r4.get("experience_context"))
check(any("需求对齐" in x for x in r4.get("experience_context", [])),
      "非标准标题下实习内容已兜底提取", r4.get("experience_context"))

print("=== 个人信息仍然剔除 ===")
blob = str(r)
for token in ("张三", "13800138000", "zhangsan@example.com", "110101199001011234",
              "某某大学", "上海市", "性别", "华为技术有限公司"):
    check(token not in blob, "输出不含 " + token)
check("匹配度" not in blob and "覆盖率" not in blob and "薪资预测" not in blob,
      "不产出画像类字段或薪资预测")
check(job_info.status()["pii_extraction"] is False, "策略仍声明不提取个人信息")
check("求职岗位" in job_info.status().get("pii_definition", ""), "策略说明岗位信息非个人信息")
check(r.get("policy", {}).get("user_data_vectorized") is False, "用户岗位字段未写入向量库")

print("=== 截图 / 入库链路保持严格 ===")
lines = job_info.job_only_lines(RESUME)
check(all("张三" not in x and "13800138000" not in x for x in lines), "入库行不含个人信息")

print("=== 前端展示不暴露 schema ===")
html = (ROOT / "workbench.html").read_text(encoding="utf-8", errors="ignore")
check("function renderParseResult" in html, "前端有可读渲染函数")
check("renderParseResult(d)" in html and "esc(pretty(d))" not in html,
      "解析结果不再以 JSON 原文展示")
check('id="insSalary"' in html, "薪资意向在洞察参数中可编辑")
check("$('#inParse').onclick" in html and "function applyParsedData" in html,
      "点「解析」触发本机模型解析并统一回填")
check("autoParseText" not in html and "createWorker" not in html.split("管理员知识入库")[0],
      "用户侧已删除浏览器 OCR 与停手自动正则解析")
mirror = (ROOT / "web" / "public" / "workbench.html").read_text(encoding="utf-8", errors="ignore")
check(mirror == html, "两份工作台页面保持同步")

print("\n=== 简历字段回归：PASS %d  FAIL %d ===" % (ok, bad))
sys.exit(0 if bad == 0 else 1)
