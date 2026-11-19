#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地账号体系单元回归：注册 / 密码登录 / 改密 / 防暴破 / 角色边界 / 加密落盘。

用临时文件替换账号库，绝不污染真实 .privacy/accounts.json。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import local_accounts as la
import auth

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


print("=== 本地账号体系单元回归 ===")

# 隔离账号库与失败计数
_tmp = Path(tempfile.gettempdir()) / "zy_accounts_test.json"
try:
    _tmp.unlink()
except OSError:
    pass
la.STORE_FILE = _tmp
la._FAILS.clear()

# 1. 注册
r = la.register("alice2026", "StrongPass123", " Alice ")
check(r["role"] == auth.ROLE_PERSONAL, "注册恒为 personal 角色")
check(r["sub"].startswith("acct:"), "sub 使用 acct: 前缀")
check(not r["demo"], "本地账号非演示身份")

# 2. 登录正确 / 错误
check(la.login("alice2026", "StrongPass123")["sub"] == r["sub"], "用户名 + 正确密码登录")
check(la.login("ALICE2026", "StrongPass123")["sub"] == r["sub"], "标识大小写不敏感")
try:
    la.login("alice2026", "wrongpass")
    check(False, "错误密码被拒")
except la.AccountError as e:
    check(e.code == "BAD_CREDENTIALS", "错误密码被拒")

# 3. 邮箱注册 + 登录
er = la.register("alice@example.com", "Passw0rd123")
check(er["role"] == auth.ROLE_PERSONAL and er["sub"].startswith("acct:"), "邮箱注册成功")
check(la.login("alice@example.com", "Passw0rd123")["sub"] == er["sub"], "邮箱登录成功")

# 4. 重名 / 弱密码 / 非法标识
try:
    la.register("alice2026", "Passw0rd123")
    check(False, "重复用户名被拒")
except la.AccountError as e:
    check(e.code == "IDENTIFIER_TAKEN", "重复用户名被拒")
for bad, code in [("ab", "BAD_IDENTIFIER"), ("goodname", "WEAK_PASSWORD")]:
    try:
        la.register(bad, "short" if bad == "goodname" else "Passw0rd123")
        check(False, "非法注册被拒: " + bad)
    except la.AccountError as e:
        check(e.code == code, "非法注册被拒: " + bad)

# 5. 改密：旧密码错误 / 正确
try:
    la.change_password(r["sub"], "wrongold", "NewPass1234")
    check(False, "错误旧密码改密被拒")
except la.AccountError as e:
    check(e.code == "BAD_CREDENTIALS", "错误旧密码改密被拒")
res = la.change_password(r["sub"], "StrongPass123", "NewPass1234")
check(res["ok"] is True, "正确旧密码改密成功")
check(la.login("alice2026", "NewPass1234")["sub"] == r["sub"], "新密码可登录")
try:
    la.login("alice2026", "StrongPass123")
    check(False, "旧密码改密后失效")
except la.AccountError:
    check(True, "旧密码改密后失效")

# 6. 防暴力破解：连续失败达阈值锁定
la._FAILS.clear()
for _ in range(la._MAX_FAILS):
    try:
        la.login("alice2026", "bad")
    except la.AccountError:
        pass
try:
    la.login("alice2026", "NewPass1234")  # 即使密码正确也被锁
    check(False, "连续失败后锁定（正确密码也被拒）")
except la.AccountError as e:
    check(e.code == "TOO_MANY_ATTEMPTS", "连续失败后锁定（正确密码也被拒）")
la._FAILS.clear()
check(la.login("alice2026", "NewPass1234")["sub"] == r["sub"], "锁定解除后可登录")

# 7. 加密落盘：文件不含明文账号/密码
raw = _tmp.read_text(encoding="utf-8") if _tmp.is_file() else ""
check("alice2026" not in raw and "alice@example.com" not in raw, "磁盘无明文账号")
check("StrongPass123" not in raw and "NewPass1234" not in raw, "磁盘无明文密码")
check("pbkdf2" in str(la.status()["password_hash"]).lower(), "状态标注 pbkdf2 哈希")

# 8. 无提权路径：注册接口不接受 role
check("role" not in la.register.__code__.co_varnames, "注册函数无 role 入参")

try:
    _tmp.unlink()
except OSError:
    pass

print("\n=== 本地账号体系单元回归：PASS %d  FAIL %d ===" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
