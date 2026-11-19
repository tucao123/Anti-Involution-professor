#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""新增全量岗位 CSV 切片入库：岗位数据_全量_20261119.csv → Qdrant 云 RAG。

数据来源：管理员/项目组主动提供的国内岗位样本 CSV（非系统爬取、非用户数据）。
全链路复用 knowledge_ingest 的确定性链路：
    PII 脱敏 → 仅岗位信息过滤 → 分块 → 向量化(public/BGE) → 出站闸门 → Qdrant(zhiyin_public_d_bge, D 级, admin_curated)

去重策略：
- 稳定 key = 岗位ID；无 ID 时回退 岗位名称|城市|岗位职责前40字。
- 独立进度文件 ingest_jobs_full_progress.json，不污染既有精选3000进度。
- `--skip-existing` 默认开启：跳过已在此前批次(精选3000等)入库的岗位ID，
  只写真正新增的岗位，避免同一岗位重复向量。用 --no-skip-existing 关闭。

用法：
  python ingest_jobs_full.py --dry-run              # 只本地切片预检，不写入
  python ingest_jobs_full.py --dry-run --limit 20   # 冒烟预检前20条
  python ingest_jobs_full.py                        # 全量写入（跳过已入库）
  python ingest_jobs_full.py --resume               # 断点续传（配合独立进度）
  python ingest_jobs_full.py --limit 50             # 只写前50条（冒烟）
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import knowledge_ingest as K
from ingest_seed_data import split_lines

# ---- 数据路径 ----
JOBS = Path(r"C:/Users/tucado/codeout/projects/Crawl-information-script/岗位/岗位数据_全量_20261119.csv")
PROGRESS = Path(__file__).with_name("ingest_jobs_full_progress.json")
# 此前批次(精选3000)进度：用于跳过已入库岗位ID
SEED_PROGRESS = Path(__file__).with_name("ingest_seed_progress.json")

BATCH_POINTS = 20  # 每批向量点数上限=outbound_guard.max_batch_points；单条块整批不入拆
WRITE_RETRIES = 6  # 境外 Qdrant 链路偶发不可达：写前强制刷新探测 + 有限重试
csv.field_size_limit(sys.maxsize)

# 构造岗位文本时使用的字段（顺序即文本顺序；均为岗位颗粒度信息，不含个人身份）
_FIELDS = [
    ("岗位名称", "岗位名称"), ("岗位族", "岗位族"), ("城市", "工作地点"),
    ("月薪", "月薪范围"), ("经验年限", "经验要求"), ("学历", "学历要求"),
]


def _build_text(row: dict) -> str:
    parts: list[str] = []
    for col, label in _FIELDS:
        val = (row.get(col, "") or "").strip()
        if val:
            parts.append("%s：%s" % (label, val))
    duty = (row.get("岗位职责", "") or "").strip()
    if duty:
        parts.append("岗位职责：\n" + "\n".join(split_lines(duty)))
    req = (row.get("任职要求", "") or "").strip()
    if req:
        parts.append("任职要求：\n" + "\n".join(split_lines(req)))
    skills = (row.get("技能标签", "") or "").strip()
    if skills:
        parts.append("技能标签：" + skills.replace("\n", "、"))
    return "\n".join(parts)


def iter_jobs():
    """逐条产出 (key, text, title, origin, job_only)。稳定 key = 岗位ID。"""
    with open(JOBS, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            jid = (row.get("岗位ID", "") or "").strip()
            name = (row.get("岗位名称", "") or "").strip()
            if jid:
                key = "id|" + jid
            else:
                key = "fb|%s|%s|%s" % (name, (row.get("城市", "") or "").strip(),
                                       (row.get("岗位职责", "") or "")[:40])
            yield key, _build_text(row), name or "岗位知识", "admin_csv", True


def load_seed_ids() -> set[str]:
    """从既有精选3000进度里提取岗位ID（key 末段），用于跳过已入库岗位。"""
    if not SEED_PROGRESS.exists():
        return set()
    try:
        keys = json.loads(SEED_PROGRESS.read_text(encoding="utf-8"))
    except Exception:
        return set()
    ids: set[str] = set()
    for k in keys:
        if k.startswith("SKIP::"):
            k = k[6:]
        parts = str(k).split("|")
        if len(parts) >= 3:
            ids.add(parts[-1].strip())
    return ids


def load_done() -> set[str]:
    if PROGRESS.exists():
        try:
            return set(json.loads(PROGRESS.read_text(encoding="utf-8")))
        except Exception:
            return set()
    return set()


def save_done(done: set[str]) -> None:
    PROGRESS.write_text(json.dumps(sorted(done), ensure_ascii=False), encoding="utf-8")


def run(dry_run, limit, resume, skip_existing, done, stats):
    import secrets
    import vector_store
    import embedding

    probe = embedding.embed(["维度探测：熟悉 Python SQL 数据分析"], scope="public")
    if str(probe.get("backend", "")).lower() != "bge" or probe.get("cloud_used"):
        raise RuntimeError("本地BGE未生效: backend=%s cloud_used=%s（请在 .env 设 EMBED_BACKEND=bge）"
                           % (probe.get("backend"), probe.get("cloud_used")))
    print("  [backend] 本地BGE dim=%d cloud_used=%s 集合=%s" %
          (probe.get("dim"), probe.get("cloud_used"),
           __import__('secure_config').cfg.get('QDRANT_COLLECTION')), flush=True)

    seed_ids = load_seed_ids() if skip_existing else set()
    print("  [dedup] 跳过此前批次已入库岗位ID=%d" % len(seed_ids), flush=True)

    ok = skipped = failed = 0
    pending: list[dict] = []
    pending_keys: list[str] = []
    processed = 0

    def flush():
        if not pending:
            return 0, []
        if dry_run:
            return len(pending), list(pending_keys)
        # 境外链路偶发不可达 + probe 30s 缓存：写前强制刷新探测，失败则退避重试，
        # 绝不把降级到 local-memory 的"假入库"当成成功。
        last = ""
        for attempt in range(1, WRITE_RETRIES + 1):
            # ingest 内部 probe() 自带 30s 缓存；正常路径不强制探测，省 2 次境外往返。
            # 只有探测缓存不可达而降级时才 force 刷新，让下一轮用上可达缓存。
            res = vector_store.ingest(pending, scope="public", source_id=K.SOURCE_ID)
            if res.get("backend") == "qdrant-cloud":
                return len(pending), list(pending_keys)
            last = "backend降级=" + str(res.get("backend")) + \
                   (" detail=" + str(res.get("detail", ""))[:80])
            pr = vector_store.probe(force=True)  # 刷新探测缓存，避免连续降级
            time.sleep(min(2 * attempt, 8))
        raise RuntimeError("写入重试%d次仍失败: %s" % (WRITE_RETRIES, last))

    def flush_if_needed(incoming: int):
        """按块数攒批：待写块 + 本条块 > 上限时先写，保证单条块不跨批、每批<=上限。"""
        if pending and len(pending) + incoming > BATCH_POINTS:
            _, written = flush()
            done.update(written)
            save_done(done)
            pending.clear()
            pending_keys.clear()

    for i, (key, text, title, origin, job_only) in enumerate(iter_jobs(), 1):
        if limit and processed >= limit:
            break
        # 断点续传：本批进度里已完成
        if resume and (key in done or "SKIP::" + key in done):
            skipped += 1
            processed += 1
            continue
        # 去重：跳过此前批次已入库的岗位ID
        if key.startswith("id|") and key[3:] in seed_ids:
            skipped += 1
            processed += 1
            stats.setdefault("skipped_detail", {})["ALREADY_INGESTED"] = \
                stats.setdefault("skipped_detail", {}).get("ALREADY_INGESTED", 0) + 1
            continue
        processed += 1
        try:
            prep = K.prepare(text, origin, title, purpose_ack=True, job_only=job_only)
            if dry_run:
                ok += 1
                continue
            chunks = prep["chunks"]
            if len(chunks) > BATCH_POINTS:
                raise RuntimeError("单条块数%d超上限%d" % (len(chunks), BATCH_POINTS))
            flush_if_needed(len(chunks))
            doc_id = "kb-" + secrets.token_hex(8)
            for j, chunk in enumerate(chunks):
                pending.append({"id": "%s-%d" % (doc_id, j), "text": chunk,
                                "title": prep["title"],
                                "material_kind": prep["material_kind"]})
            pending_keys.append(key)
            if processed % 500 == 0 or processed == 1:
                print("  [%s] %4d 处理 / %d skip / %d fail  待写块=%d" %
                      ("jobs_full", ok, skipped, failed, len(pending)), flush=True)
        except Exception as exc:
            msg = str(exc)
            if isinstance(exc, K.KnowledgeRejected) and msg in (
                    "NO_CHUNK", "NO_JOB_INFO", "TEXT_LENGTH_INVALID", "NO_TEXT",
                    "TOO_MANY_CHUNKS", "IMAGE_PAYLOAD_REJECTED"):
                done.add("SKIP::" + key)
                save_done(done)
                skipped += 1
                sd = stats.setdefault("skipped_detail", {})
                sd[msg] = sd.get(msg, 0) + 1
                continue
            # 写入类异常（网络/闸门）不吞：整批未落盘、done 未更新，直接终止由
            # --resume 重跑，避免 pending 跨批累积或把失败误记为单条跳过。
            if isinstance(exc, RuntimeError) and ("写入重试" in msg or "单条块数" in msg):
                print("  [jobs_full] 写入异常终止 #%d: %s" % (i, msg), flush=True)
                raise
            failed += 1
            stats["errors"].append("jobs_full #%d: %s: %s" % (i, type(exc).__name__, msg))
            if failed <= 5:
                print("  [jobs_full] FAIL #%d %s: %s" % (i, type(exc).__name__, msg), flush=True)

    if pending:
        try:
            _, written = flush()
            done.update(written)
            save_done(done)
        except Exception as exc:
            failed += 1
            stats["errors"].append("jobs_full flush: %s: %s" % (type(exc).__name__, exc))
            print("  [jobs_full] flush FAIL: %s" % exc, flush=True)

    ok = processed - skipped - failed
    stats["jobs_full"] = {"ok": ok, "skipped": skipped, "failed": failed}
    print("[jobs_full] 完成: ok=%d skip=%d fail=%d" % (ok, skipped, failed), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只本地切片预检，不联网写入")
    ap.add_argument("--limit", type=int, default=0, help="最多处理 N 条新记录（冒烟）")
    ap.add_argument("--resume", action="store_true", help="跳过本批进度已处理记录")
    ap.add_argument("--no-skip-existing", action="store_true",
                    help="不跳过此前批次已入库岗位ID（默认跳过以避免重复向量）")
    args = ap.parse_args()

    print("模式:", "DRY-RUN（不写入）" if args.dry_run else "写入 Qdrant 云", flush=True)
    st = K.status()
    print("后端:", st.get("backend_in_use"), "| 云写入:", st.get("cloud_writes_enabled"),
          "| 集合:", st.get("collection"), flush=True)

    done = load_done() if args.resume else set()
    stats: dict = {"errors": []}
    run(args.dry_run, args.limit, args.resume, not args.no_skip_existing, done, stats)
    if not args.dry_run:
        save_done(done)

    print("\n===== 汇总 =====", flush=True)
    for k, v in stats.items():
        if k in ("errors", "skipped_detail"):
            continue
        print("%-12s ok=%5d skip=%5d fail=%4d" % (k, v["ok"], v["skipped"], v["failed"]))
    if stats.get("skipped_detail"):
        print("跳过明细:", stats["skipped_detail"])
    if stats["errors"]:
        print("失败明细(前10):")
        for e in stats["errors"][:10]:
            print("  -", e)
        sys.exit(1)
    print("全部完成，无失败。" if not args.dry_run else "预检完成，未写入。")


if __name__ == "__main__":
    main()
