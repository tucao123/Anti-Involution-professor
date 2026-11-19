#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""管理员种子数据切片入库：国内岗位样本 + 面试话术 → Qdrant 云 RAG。

数据来源（均由管理员/项目组主动提供，非系统爬取）：
  1. 中国公共招聘网计算机类岗位精选 3000（内容级去重、采集时已 PII 清洗）
  2. 面试话术库.md / 通用话术.csv / 质询流程.csv（公开面试技巧要点重排）

全部复用 knowledge_ingest 的确定性链路：PII 脱敏 → （岗位走）仅岗位信息过滤
→ 分块 → 向量化 → 出站闸门 → Qdrant(zhiyin_public_d, D 级, admin_curated)。

用法：
  python ingest_seed_data.py --dry-run          # 只本地预检切片，不写入
  python ingest_seed_data.py                    # 全量写入
  python ingest_seed_data.py --only jobs        # 只写岗位
  python ingest_seed_data.py --only talk        # 只写话术
  python ingest_seed_data.py --limit 50         # 只处理前 N 条（冒烟）
  python ingest_seed_data.py --resume           # 跳过已记录入库的记录（断点续传）
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

import knowledge_ingest as K

# ---- 数据路径 ----
BASE = Path(r"C:/Users/tucado/codeout/projects/Crawl-information-script")
JOBS = BASE / "岗位" / "岗位数据_计算机_精选3000_20261117.csv"
TALK_MD = BASE / "output" / "面试话术库.md"
TALK_CSV = BASE / "output" / "通用话术.csv"
FLOW_CSV = BASE / "output" / "质询流程.csv"

# 断点文件：只记录稳定 key 的哈希，不写任何原文
PROGRESS = Path(__file__).with_name("ingest_seed_progress.json")

_SENT_SPLIT = re.compile(r"(?<=[。；;！!？?\n])|\s+(?=[一二三四五六七八九十]、|\d+[．.、])")


def split_lines(desc: str, maxlen: int = 420) -> list[str]:
    """把长段落拆成 <500 字的岗位语境行，避免 job_only 过滤整行丢弃。"""
    parts = _SENT_SPLIT.split(desc)
    out: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        while len(p) > maxlen:
            cut = p.rfind("，", 0, maxlen)
            if cut < maxlen // 2:
                cut = maxlen
            out.append(p[:cut])
            p = p[cut:].lstrip("，")
        if p:
            out.append(p)
    return out


def iter_jobs():
    """逐条产出岗位文本。稳定 key = 岗位名+城市+岗位ID。"""
    with open(JOBS, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            key = "|".join([row.get("岗位名称", ""), row.get("城市", ""), row.get("岗位ID", "")])
            desc = split_lines(row.get("岗位描述", ""))
            text = "岗位名称：%s\n工作地点：%s\n岗位职责与任职要求：\n%s" % (
                row.get("岗位名称", ""), row.get("城市", ""), "\n".join(desc))
            yield key, text, row.get("岗位名称", "岗位知识"), "admin_csv", True


def iter_talk_md():
    """按 ### 小节切面试话术库（跳过目录与空章节）。"""
    text = TALK_MD.read_text(encoding="utf-8")
    sections = re.split(r"(?m)^###\s+", text)
    for sec in sections[1:]:
        lines = sec.splitlines()
        title = lines[0].strip()[:60]
        body = "\n".join(lines[1:]).strip()
        if len(body) < 20:
            continue
        # 去掉 markdown 链接与锚点噪音，保留可读正文
        body = re.sub(r"<a id=.*?</a>", "", body)
        yield "md|" + title, body, "话术·" + title, "interview_material", False


def iter_talk_csv():
    with open(TALK_CSV, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            key = "csv|通用|" + row.get("编号", "")
            fields = ["编号", "模块", "场景", "面试官典型问法", "考察点", "应答结构",
                      "话术模板（XX 处替换为你的经历）", "记忆点/关键句", "禁忌", "适用岗位"]
            parts = ["%s：%s" % (k, row.get(k, "")) for k in fields if row.get(k, "").strip()]
            yield key, "\n".join(parts), "话术·" + row.get("场景", row.get("编号", "")), "interview_material", False


def iter_flow_csv():
    with open(FLOW_CSV, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            key = "csv|流程|%s-%s" % (row.get("阶段", ""), row.get("步骤", ""))
            title = "%s %s" % (row.get("阶段名称", ""), row.get("环节", ""))
            fields = ["阶段", "阶段名称", "时间窗", "环节", "面试官动作（施压手段）",
                      "潜台词 / 考察点", "你的目标", "具体动作", "参考话术", "节奏", "禁忌"]
            parts = ["%s：%s" % (k, row.get(k, "")) for k in fields if row.get(k, "").strip()]
            yield key, "\n".join(parts), "流程·" + title, "interview_material", False


def dedup_records(records, seen: set[str]):
    """按稳定 key 去重，同一岗位/话术只保留首次。"""
    for key, text, title, origin, job_only in records:
        if key in seen:
            continue
        seen.add(key)
        yield key, text, title, origin, job_only


def load_done() -> set[str]:
    if PROGRESS.exists():
        try:
            return set(json.loads(PROGRESS.read_text(encoding="utf-8")))
        except Exception:
            return set()
    return set()


def save_done(done: set[str]) -> None:
    PROGRESS.write_text(json.dumps(sorted(done), ensure_ascii=False), encoding="utf-8")


BATCH = 10  # 每批写入向量点数：实测稳定走 Qdrant 云且不超时


def run_group(name, records, dry_run, limit, done, resume, stats):
    """本地 prepare 攒块 → 按 BATCH 批量写云（复用同一套出站闸门）。

    断点只在「整批写云成功」后落盘：pending 里攒的块若写入失败/降级，
    对应记录不进 done，重跑 --resume 时会重试，绝不产生「标了完成但没入库」。
    """
    import secrets
    import vector_store

    ok = skipped = failed = 0
    pending: list[dict] = []          # 攒块 [{id,text,title}]
    pending_keys: list[str] = []      # 与 pending 对应的记录 key（整批一起落断点）
    processed = 0

    import embedding

    # 启动时确认走本地 BGE：本地模型不依赖网络、无维度漂移。
    probe = embedding.embed(["维度探测：熟悉 Python SQL 数据分析"], scope="public")
    if str(probe.get("backend", "")).lower() != "bge" or probe.get("cloud_used"):
        raise RuntimeError("本地BGE未生效: backend=%s cloud_used=%s（请在 .env 设 EMBED_BACKEND=bge）"
                           % (probe.get("backend"), probe.get("cloud_used")))
    print("  [backend] 本地BGE dim=%d cloud_used=%s 集合=%s" %
          (probe.get("dim"), probe.get("cloud_used"),
           __import__('secure_config').cfg.get('QDRANT_COLLECTION')), flush=True)

    def flush():
        """写一批；成功返回 (块数, key列表)，失败抛异常且不改 pending。"""
        if not pending:
            return 0, []
        if dry_run:
            return len(pending), list(pending_keys)
        res = vector_store.ingest(pending, scope="public", source_id=K.SOURCE_ID)
        if res.get("backend") != "qdrant-cloud":
            raise RuntimeError("backend降级=" + str(res.get("backend")) +
                               (" detail=" + str(res.get("detail", ""))[:60]))
        return len(pending), list(pending_keys)

    for i, (key, text, title, origin, job_only) in enumerate(records, 1):
        if limit and processed >= limit:
            break
        if resume and (key in done or "SKIP::" + key in done):
            skipped += 1
            processed += 1
            continue
        processed += 1
        try:
            prep = K.prepare(text, origin, title, purpose_ack=True, job_only=job_only)
            if dry_run:
                ok += 1
                continue
            doc_id = "kb-" + secrets.token_hex(8)
            for j, chunk in enumerate(prep["chunks"]):
                pending.append({"id": "%s-%d" % (doc_id, j), "text": chunk,
                                "title": prep["title"],
                                "material_kind": prep["material_kind"]})
            pending_keys.append(key)
            if len(pending) >= BATCH:
                _, written_keys = flush()
                done.update(written_keys)
                save_done(done)
                pending.clear()
                pending_keys.clear()
            if processed % 500 == 0 or processed == 1:
                print("  [%s] %4d 处理 / %d skip / %d fail  待写块=%d" %
                      (name, ok, skipped, failed, len(pending)), flush=True)
        except Exception as exc:
            msg = str(exc)
            # 确定性拒绝（数据本身信息不足/不合规）：标记跳过，resume 不再重试
            if isinstance(exc, K.KnowledgeRejected) and msg in (
                    "NO_CHUNK", "NO_JOB_INFO", "TEXT_LENGTH_INVALID", "NO_TEXT"):
                done.add("SKIP::" + key)
                save_done(done)
                skipped += 1
                sd = stats.setdefault("skipped_detail", {})
                sd[msg] = sd.get(msg, 0) + 1
                continue
            failed += 1
            stats["errors"].append("%s #%d: %s: %s" % (name, i, type(exc).__name__, msg))
            if failed <= 5:
                print("  [%s] FAIL #%d %s: %s" % (name, i, type(exc).__name__, msg), flush=True)

    # 收尾：把不足一批的块写出
    if pending:
        try:
            _, written_keys = flush()
            done.update(written_keys)
            save_done(done)
        except Exception as exc:
            failed += 1
            stats["errors"].append("%s flush: %s: %s" % (name, type(exc).__name__, exc))
            print("  [%s] flush FAIL: %s" % (name, exc), flush=True)

    ok = processed - skipped - failed
    stats[name] = {"ok": ok, "skipped": skipped, "failed": failed}
    print("[%s] 完成: ok=%d skip=%d fail=%d" % (name, ok, skipped, failed), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只本地切片预检，不联网写入")
    ap.add_argument("--only", choices=["jobs", "talk"], help="只跑某一类")
    ap.add_argument("--limit", type=int, default=0, help="每类最多处理 N 条（冒烟）")
    ap.add_argument("--resume", action="store_true", help="跳过已入库记录")
    args = ap.parse_args()

    print("模式:", "DRY-RUN（不写入）" if args.dry_run else "写入 Qdrant 云", flush=True)
    st = K.status()
    print("后端:", st.get("backend_in_use"), "| 云写入:", st.get("cloud_writes_enabled"),
          "| 集合:", st.get("collection"), flush=True)

    done = load_done() if args.resume else set()
    stats: dict = {"errors": []}

    seen: set[str] = set()
    if args.only in (None, "jobs"):
        print("\n=== 岗位数据：精选3000 ===", flush=True)
        run_group("jobs", dedup_records(iter_jobs(), seen), args.dry_run, args.limit, done, args.resume, stats)

    if args.only in (None, "talk"):
        print("\n=== 面试话术库.md ===", flush=True)
        run_group("talk_md", dedup_records(iter_talk_md(), seen), args.dry_run, args.limit, done, args.resume, stats)
        print("\n=== 通用话术.csv ===", flush=True)
        run_group("talk_csv", dedup_records(iter_talk_csv(), seen), args.dry_run, args.limit, done, args.resume, stats)
        print("\n=== 质询流程.csv ===", flush=True)
        run_group("flow_csv", dedup_records(iter_flow_csv(), seen), args.dry_run, args.limit, done, args.resume, stats)

    if not args.dry_run:
        save_done(done)

    print("\n===== 汇总 =====", flush=True)
    total_ok = 0
    for k, v in stats.items():
        if k in ("errors", "skipped_detail"):
            continue
        total_ok += v["ok"]
        print("%-10s ok=%4d skip=%4d fail=%3d" % (k, v["ok"], v["skipped"], v["failed"]))
    print("总成功入库记录:", total_ok)
    if stats.get("skipped_detail"):
        print("确定性跳过(数据信息不足/去重):", stats["skipped_detail"])
    if stats["errors"]:
        print("失败明细(前10):")
        for e in stats["errors"][:10]:
            print("  -", e)
        sys.exit(1)
    print("全部完成，无失败。" if not args.dry_run else "预检完成，未写入。")


if __name__ == "__main__":
    main()
