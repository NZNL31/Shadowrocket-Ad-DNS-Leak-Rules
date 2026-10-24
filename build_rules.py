#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_rules.py —— Shadowrocket 广告/白名单规则集构建器

相比旧的 merge_rules.py，本脚本：

  1. 不再"读自己、写自己"。旧脚本把 a-nomad.conf 既当输入又当输出，
     导致每天只增不减：源里已删除的广告域名永远留在配置里。
  2. 每次从上游重新构建，天然幂等，文件体积恒定。
  3. 把 18 万条规则从 .conf 里拆出来，变成独立的 .list 规则集，
     主配置回到 ~100 行，订阅下载从 7MB 降到 ~10KB。
  4. 白名单（Proxy/DIRECT）单独成表并排在广告规则之前，
     避免 whitelist 被更宽的父域 Reject 规则吞掉。
  5. 清洗脏数据：去掉行尾注释（上游有 `...,PROXY # Google AMP issue#237`
     这种把注释当策略的坏行）、统一小写、去重、丢弃非法行。
  6. 构建后自检：规则数突变、空策略、产物格式都会拦下来。

用法：
    python build_rules.py                  # 从本地 sr_cnip_ad.conf 构建
    python build_rules.py --url            # 先下载上游最新版再构建
    python build_rules.py --inline         # 额外产出单文件版 a-nomad.full.conf
    python build_rules.py --check          # 只校验已有产物
"""

import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone, timedelta

# ---------------------------------------------------------------- 常量配置

UPSTREAM_URL = (
    "https://johnshall.github.io/"
    "Shadowrocket-ADBlock-Rules-Forever/sr_cnip_ad.conf"
)

SRC_FILE = "sr_cnip_ad.conf"
CONF_FILE = "a-nomad.conf"
AD_LIST = "ad.list"
ALLOW_LIST = "unban.list"
META_FILE = "meta.json"
INLINE_OUT = "a-nomad.full.conf"

# 视为"拦截"的策略名（小写比较）
REJECT_POLICIES = {
    "reject", "reject-drop", "reject-no-drop", "reject-tinygif",
}
# 视为"放行/白名单"的策略名（小写比较）
ALLOW_POLICIES = {"proxy", "direct"}

DOMAIN_TYPES = {"DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD"}
IP_TYPES = {"IP-CIDR", "IP-CIDR6"}
SUPPORTED_TYPES = DOMAIN_TYPES | IP_TYPES

# 域名合法性：不含空白、逗号、井号；至少含一个点或本身就是 TLD
DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9\-_]*[a-z0-9])?(\.[a-z0-9]([a-z0-9\-_]*[a-z0-9])?)*$")
KEYWORD_RE = re.compile(r"^[a-z0-9\-_.]{2,64}$")
CIDR_RE = re.compile(r"^[0-9a-f:.]+/\d{1,3}$")

# 产物校验阈值
MIN_EXPECTED_AD = 10_000          # 广告规则少于这个数基本就是上游挂了
MAX_DRIFT = 0.40                  # 与上次相比波动超过 40% 视为异常


# ---------------------------------------------------------------- 工具函数

def cst_now() -> str:
    """北京时间字符串，写进产物头部，方便排查是哪天的规则。"""
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S CST")


def fetch_upstream(url: str, dest: str) -> None:
    print(f"[1/5] 下载上游规则 {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "shadowrocket-rules-builder/2.0"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = resp.read()
    if len(data) < 100_000:
        raise SystemExit(f"上游文件只有 {len(data)} 字节，疑似下载失败，已中止")
    with open(dest, "wb") as f:
        f.write(data)
    print(f"      -> {dest}  {len(data) / 1048576:.2f} MB")


def parse_rule(raw: str):
    """
    解析一行规则，返回 (type, value, policy_lower) 或 None。

    关键点：先砍掉行尾注释。上游确实存在
        DOMAIN-SUFFIX,ampproject.org,PROXY # Google AMP issue#237
    这种行，旧脚本原样合并进配置，策略字段变成
        "PROXY # Google AMP issue#237"
    对客户端来说是非法策略，这条规则等于废掉。
    """
    line = raw.split("#", 1)[0].strip()
    if not line or line.startswith("["):
        return None

    parts = [p.strip() for p in line.split(",")]
    if len(parts) < 3:
        return None

    rtype = parts[0].upper()
    if rtype not in SUPPORTED_TYPES:
        return None

    value = parts[1].lower().strip()
    policy = parts[2].lower().strip()

    # IP-CIDR,1.2.3.0/24,Proxy,no-resolve 之类，第三个字段才是策略
    if policy in ("no-resolve", "src") and len(parts) >= 4:
        policy = parts[3].lower().strip()
        if policy in ("no-resolve", "src"):
            return None

    if not value or not policy:
        return None

    # 值合法性
    if rtype in IP_TYPES:
        if not CIDR_RE.match(value):
            return None
    elif rtype == "DOMAIN-KEYWORD":
        if not KEYWORD_RE.match(value):
            return None
    else:
        if not DOMAIN_RE.match(value) or len(value) > 253 or "." not in value:
            return None

    return rtype, value, policy


def load_rules(src: str):
    """读取源配置，返回 (ad_rules, allow_rules, stats)。"""
    ad = {}        # (type, value) -> policy
    allow = {}
    stats = {"total": 0, "dropped": 0, "unknown_policy": 0, "dup": 0}

    in_rule_section = False
    with open(src, encoding="utf-8") as f:
        for raw in f:
            s = raw.strip()
            if s.startswith("[") and s.endswith("]"):
                in_rule_section = s.lower() == "[rule]"
                continue
            if not in_rule_section or not s or s.startswith("#"):
                continue

            stats["total"] += 1
            parsed = parse_rule(s)
            if parsed is None:
                stats["dropped"] += 1
                continue

            rtype, value, policy = parsed
            key = (rtype, value)

            if policy in REJECT_POLICIES:
                bucket = ad
            elif policy in ALLOW_POLICIES:
                bucket = allow
            else:
                # GEOIP/FINAL 等不参与合并，策略未知的行直接丢弃
                stats["unknown_policy"] += 1
                continue

            if key in bucket:
                stats["dup"] += 1
            bucket[key] = policy

    return ad, allow, stats


def build_outputs(ad, allow):
    """把规则整理成两个列表文件的内容。"""
    # 白名单：关键字 > IP > 域名后缀。顺序很重要，必须排在广告规则前面。
    allow_kw = sorted(v for t, v in allow if t == "DOMAIN-KEYWORD")
    allow_ip = sorted(v for t, v in allow if t in IP_TYPES)
    allow_dom = sorted(v for t, v in allow if t in DOMAIN_TYPES and t != "DOMAIN-KEYWORD")

    # 广告规则里如果和精确白名单域名重合，直接剔除，少一条是一条
    allow_set = set(allow_dom)
    ad_dom = sorted(v for t, v in ad if t in DOMAIN_TYPES and v not in allow_set)
    ad_ip = sorted(v for t, v in ad if t in IP_TYPES)

    header = (
        "# NAME: {name}\n"
        "# 自动生成，请勿手动编辑（构建脚本 build_rules.py）\n"
        "# 上游: johnshall/Shadowrocket-ADBlock-Rules-Forever\n"
        "# 构建: {ts}\n"
        "# 规则数: {n}\n"
    )

    allow_lines = [f"DOMAIN-KEYWORD,{v}" for v in allow_kw]
    allow_lines += [f"IP-CIDR,{v}" for v in allow_ip]
    allow_lines += [f"DOMAIN-SUFFIX,{v}" for v in allow_dom]

    ad_lines = [f"IP-CIDR,{v}" for v in ad_ip]
    ad_lines += [f"DOMAIN-SUFFIX,{v}" for v in ad_dom]

    ts = cst_now()
    allow_text = header.format(name=ALLOW_LIST, ts=ts, n=len(allow_lines)) + "\n".join(allow_lines) + "\n"
    ad_text = header.format(name=AD_LIST, ts=ts, n=len(ad_lines)) + "\n".join(ad_lines) + "\n"
    return ad_text, allow_text, len(ad_lines), len(allow_lines)


def atomic_write(path: str, text: str) -> None:
    """先写临时文件再改名，避免构建中断留下半个文件被 Pages 发布出去。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def validate(ad_text: str, allow_text: str, ad_count: int, allow_count: int) -> None:
    """产物自检。任何一项不过就直接让 CI 失败，不要发布坏规则。"""
    problems = []

    if ad_count < MIN_EXPECTED_AD:
        problems.append(f"广告规则只有 {ad_count} 条，低于阈值 {MIN_EXPECTED_AD}")
    if allow_count == 0:
        problems.append("白名单为空")

    bad = []
    for name, text in ((AD_LIST, ad_text), (ALLOW_LIST, allow_text)):
        for line in text.splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if s.count(",") != 1:
                bad.append(f"{name}: {s}")
            elif s.split(",")[0] not in ("DOMAIN-SUFFIX", "DOMAIN-KEYWORD", "IP-CIDR", "IP-CIDR6"):
                bad.append(f"{name}: {s}")
            elif "/" in s.split(",")[1] and s.split(",")[0] not in ("IP-CIDR", "IP-CIDR6"):
                bad.append(f"{name}: CIDR 被写成了域名规则 {s}")
    if bad:
        problems.append(f"有 {len(bad)} 行格式非法，例如 {bad[:3]}")

    # 与上次构建对比，防止上游挂掉导致规则被清空
    if os.path.exists(META_FILE):
        try:
            prev = json.load(open(META_FILE, encoding="utf-8"))
            prev_n = prev.get("ad_rules", 0)
            if prev_n:
                drift = abs(ad_count - prev_n) / prev_n
                if drift > MAX_DRIFT:
                    problems.append(
                        f"广告规则数从 {prev_n} 变到 {ad_count}（波动 {drift:.0%}），超过 {MAX_DRIFT:.0%} 阈值"
                    )
        except Exception:
            pass

    if problems:
        print("\n[校验失败]", file=sys.stderr)
        for p in problems:
            print("  -", p, file=sys.stderr)
        raise SystemExit(1)

    print("[5/5] 校验通过")


def check_conf(conf_path: str) -> None:
    """检查主配置结构是否完整。"""
    if not os.path.exists(conf_path):
        print(f"跳过：{conf_path} 不存在")
        return
    text = open(conf_path, encoding="utf-8").read()
    need = ["[General]", "[Proxy Group]", "[Rule]", "FINAL,"]
    missing = [k for k in need if k not in text]
    lines = text.count("\n") + 1
    print(f"[check] {conf_path}: {lines} 行, {len(text.encode()) / 1024:.1f} KB")
    if missing:
        raise SystemExit(f"[check] 缺少必要内容: {missing}")
    print("[check] 结构完整")


def expand_inline(conf_path: str, ad_text: str, allow_text: str, out_path: str) -> None:
    """
    生成单文件版：把两个 .list 的内容原地展开进 .conf。
    给不想依赖远程规则集的人用，体积约 1.5MB（仍然远小于原来的 7MB）。
    """
    ad_lines = [l for l in ad_text.splitlines() if l and not l.startswith("#")]
    allow_lines = [l for l in allow_text.splitlines() if l and not l.startswith("#")]

    # 注意：不能用 "ad.list" in line 判断，Download.list 里也含 "ad.list"，
    # 会把规则重复展开一遍。这里用完整路径做锚点。
    ad_marker = "/Shadowrocket-Ad-DNS-Leak-Rules/ad.list"
    allow_marker = "/Shadowrocket-Ad-DNS-Leak-Rules/unban.list"

    out = []
    for line in open(conf_path, encoding="utf-8").read().splitlines():
        if ad_marker in line:
            out.append("# >> 广告规则（内联展开）")
            out.extend(f"{l}, 🛑 广告拦截" for l in ad_lines)
        elif allow_marker in line:
            out.append("# >> 白名单（内联展开，必须先于广告规则）")
            out.extend(f"{l}, 🎯 全球直连" for l in allow_lines)
        else:
            out.append(line)

    atomic_write(out_path, "\n".join(out) + "\n")
    size = os.path.getsize(out_path) / 1048576
    print(f"[inline] 已生成 {out_path}  {size:.2f} MB")


# ---------------------------------------------------------------- 主流程

def main() -> None:
    ap = argparse.ArgumentParser(description="构建 Shadowrocket 广告规则集")
    ap.add_argument("--url", action="store_true", help="先从上游下载最新 sr_cnip_ad.conf")
    ap.add_argument("--src", default=SRC_FILE, help="源配置路径")
    ap.add_argument("--conf", default=CONF_FILE, help="主配置路径")
    ap.add_argument("--inline", action="store_true", help="额外生成单文件版配置")
    ap.add_argument("--check", action="store_true", help="只校验，不构建")
    args = ap.parse_args()

    if args.check:
        for p in (AD_LIST, ALLOW_LIST):
            if not os.path.exists(p):
                raise SystemExit(f"[check] 缺少产物 {p}")
        print(f"[check] {AD_LIST}: {sum(1 for l in open(AD_LIST, encoding='utf-8') if l.strip() and not l.startswith('#'))} 条")
        print(f"[check] {ALLOW_LIST}: {sum(1 for l in open(ALLOW_LIST, encoding='utf-8') if l.strip() and not l.startswith('#'))} 条")
        check_conf(args.conf)
        return

    if args.url:
        fetch_upstream(UPSTREAM_URL, args.src)
    elif not os.path.exists(args.src):
        raise SystemExit(f"找不到 {args.src}，加 --url 从上游下载")

    print(f"[2/5] 解析 {args.src}")
    ad, allow, stats = load_rules(args.src)
    print(
        f"      原始 {stats['total']} 行 | 丢弃非法 {stats['dropped']} "
        f"| 未知策略 {stats['unknown_policy']} | 重复 {stats['dup']}"
    )
    print(f"      广告域名 {len(ad)} 条 | 白名单 {len(allow)} 条")

    print("[3/5] 生成规则集")
    ad_text, allow_text, ad_n, allow_n = build_outputs(ad, allow)

    print("[4/5] 写入产物")
    atomic_write(AD_LIST, ad_text)
    atomic_write(ALLOW_LIST, allow_text)

    meta = {
        "build_time": cst_now(),
        "ad_rules": ad_n,
        "allow_rules": allow_n,
        "source_lines": stats["total"],
        "dropped": stats["dropped"],
    }
    atomic_write(META_FILE, json.dumps(meta, ensure_ascii=False, indent=2) + "\n")

    print(f"      {AD_LIST}: {ad_n} 条  {os.path.getsize(AD_LIST) / 1048576:.2f} MB")
    print(f"      {ALLOW_LIST}: {allow_n} 条  {os.path.getsize(ALLOW_LIST) / 1024:.1f} KB")

    validate(ad_text, allow_text, ad_n, allow_n)

    if args.inline:
        expand_inline(args.conf, ad_text, allow_text, INLINE_OUT)

    check_conf(args.conf)
    print("\n完成。")


if __name__ == "__main__":
    main()
