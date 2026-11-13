#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_rules.py —— Shadowrocket 广告 / 白名单 / 代理规则集构建器  v2.1.0

v2.1.0 相对 v2.0.0 的四个修正
---------------------------------------------------------------------------
1. 策略不再合并。v2.0.0 把上游的 Proxy 和 DIRECT 都塞进同一个 unban.list，
   而 a-nomad.conf 又给整张表配了「🎯 全球直连」。结果是上游要求走代理的
   telegram / disneyplus / amazon·aws / raw.githubusercontent.com 全部直连。
   现在分三个桶：ad.list（拦截）、unban.list（直连）、proxy.list（代理）。

2. 规则类型不再被放大。v2.0.0 把 DOMAIN（精确）也写成 DOMAIN-SUFFIX，
   一条精确规则会波及全部子域。现在按原类型输出。

3. 广告侧的 DOMAIN-KEYWORD 不再静默丢弃（v2.0.0 只对白名单侧生效）。

4. IP 类规则不再进入 .list。规则集里混 IP-CIDR 会迫使客户端为每个域名
   做一次本地解析——这是 DNS 泄漏和首包变慢的直接原因。IP 规则改为
   `--emit-ip` 打印出来，人工放进 conf 末尾并带 no-resolve。

另外新增：--update-readme（把条数写回 README）、--selftest（跑内置断言示例）。

用法
---------------------------------------------------------------------------
    python build_rules.py                    # 用本地 sr_cnip_ad.conf 构建
    python build_rules.py --url               # 先下载上游最新版再构建
    python build_rules.py --url --inline      # 额外生成单文件版
    python build_rules.py --check             # 只校验已有产物 + 主配置
    python build_rules.py --update-readme     # 把 meta.json 的条数写回 README
    python build_rules.py --emit-ip           # 打印可粘贴的 IP 规则（带 no-resolve）
"""

import argparse
import json
import os
import re
import sys
import urllib.request
from collections import defaultdict

# ---------------------------------------------------------------- 常量配置

UPSTREAM_URL = (
    "https://johnshall.github.io/"
    "Shadowrocket-ADBlock-Rules-Forever/sr_cnip_ad.conf"
)

SRC_FILE = "sr_cnip_ad.conf"
CONF_FILE = "a-nomad.conf"
AD_LIST = "ad.list"
ALLOW_LIST = "unban.list"
PROXY_LIST = "proxy.list"
META_FILE = "meta.json"
INLINE_OUT = "a-nomad.full.conf"
README_FILE = "README.md"

# 视为「拦截」的策略名（小写比较）
REJECT_POLICIES = {"reject", "reject-drop", "reject-no-drop", "reject-tinygif"}
# 视为「直连」的策略名
DIRECT_POLICIES = {"direct"}
# 视为「代理」的策略名
PROXY_POLICIES = {"proxy"}

DOMAIN_TYPES = {"DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD"}
IP_TYPES = {"IP-CIDR", "IP-CIDR6"}
SUPPORTED_TYPES = DOMAIN_TYPES | IP_TYPES

# 输出顺序：精确 → 后缀 → 关键词（同一张表内不会互相覆盖，顺序只影响可读性）
TYPE_ORDER = ("DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD")

# 规则里第三字段可能是选项而不是策略
RULE_OPTIONS = {"no-resolve", "src"}

DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9\-_]*[a-z0-9])?(\.[a-z0-9]([a-z0-9\-_]*[a-z0-9])?)*$")
KEYWORD_RE = re.compile(r"^[a-z0-9\-_.]{2,64}$")
CIDR_RE = re.compile(r"^[0-9a-f:.]+/\d{1,3}$")

# 产物校验阈值
MIN_EXPECTED_AD = 20_000      # 广告规则少于这个数基本就是上游挂了
MAX_DRIFT = 0.40              # 与上次相比波动超过 40% 视为异常

# 防泄漏自检站点：必须出现在 proxy.list，否则「测泄漏」的流量本身走直连，
# 结果永远是本地 DNS。这是 v2.0.0 最隐蔽的一个坑。
LEAK_TEST_DOMAINS = (
    "browserleaks.com",
    "browserleaks.org",
    "dnsleaktest.com",
    "ipleak.net",
)

# 关键条目存在性断言：上游改结构导致整块丢失时立刻报警
MUST_HAVE = {
    AD_LIST: ("doubleclick.net", "googlesyndication.com"),
    PROXY_LIST: ("telegram.org",),
}

# 上游配置头部的构建时间，形如 "# build time: UTC 2026-10-23 23:04:12"
BUILD_TIME_RE = re.compile(r"^#\s*build time:\s*(.+)$", re.I)

README_START = "<!-- RULES:START -->"
README_END = "<!-- RULES:END -->"


# ---------------------------------------------------------------- 工具函数

def fetch_upstream(url: str, dest: str) -> None:
    print(f"[1/6] 下载上游规则 {url}")
    req = urllib.request.Request(
        url, headers={"User-Agent": "shadowrocket-rules-builder/2.1"}
    )
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

    # IP-CIDR,1.2.3.0/24,Proxy,no-resolve 之类：跳过选项字段找策略
    i = 2
    while policy in RULE_OPTIONS:
        i += 1
        if i >= len(parts):
            return None
        policy = parts[i].lower().strip()

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
    """
    读取源配置，返回 (ad, direct, proxy, ip_rules, stats)。

    三个桶各自是 {(type, value): policy} 的字典，天然去重。
    """
    ad, direct, proxy, ip_rules = {}, {}, {}, {}
    stats = {"total": 0, "dropped": 0, "unknown_policy": 0, "dup": 0, "ip_in_lists": 0}

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
            elif policy in DIRECT_POLICIES:
                bucket = direct
            elif policy in PROXY_POLICIES:
                bucket = proxy
            else:
                stats["unknown_policy"] += 1
                continue

            if key in bucket:
                stats["dup"] += 1
            bucket[key] = policy

            # IP 规则单独记账，绝不写进 .list
            if rtype in IP_TYPES:
                ip_rules[key] = policy
                stats["ip_in_lists"] += 1

    return ad, direct, proxy, ip_rules, stats


def read_source_build_time(src: str) -> str:
    """
    从上游配置头部读 "# build time: UTC ..."。

    产物头部用上游的构建时间而不是本地时间，这样只要上游没变，
    重新构建出来的文件就是逐字节相同的，CI 才能靠 git diff 判断
    「规则无变化」并跳过提交，不会每天产生一个只有时间戳的空提交。
    """
    try:
        with open(src, encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i > 12:
                    break
                m = BUILD_TIME_RE.match(line.strip())
                if m:
                    return m.group(1).strip()
    except OSError:
        pass
    return "unknown"


def render(rules: dict):
    """把 {(type, value): policy} 渲染成规则行，保留原类型。"""
    lines = []
    counts = defaultdict(int)
    for t in TYPE_ORDER:
        for key in sorted(k for k in rules if k[0] == t):
            lines.append(f"{t},{key[1]}")
            counts[t] += 1
    return lines, counts


def header(name: str, stamp: str, n: int) -> str:
    return (
        f"# NAME: {name}\n"
        "# 自动生成，请勿手动编辑（构建脚本 build_rules.py）\n"
        "# 上游: johnshall/Shadowrocket-ADBlock-Rules-Forever\n"
        f"# 上游构建: {stamp}\n"
        f"# 规则数: {n}\n"
        "# 格式: 仅域名类规则（DOMAIN / DOMAIN-SUFFIX / DOMAIN-KEYWORD），"
        "不含 IP-CIDR，避免客户端为匹配而提前做 DNS 解析\n"
    )


def build_outputs(ad, direct, proxy, stamp):
    """
    生成三个规则集。返回 (texts, counts)：

    texts  = {AD_LIST: str, ALLOW_LIST: str, PROXY_LIST: str}
    counts = {"ad": {...}, "allow": {...}, "proxy": {...}}
    """
    texts, counts = {}, {}

    for name, rules in ((AD_LIST, ad), (ALLOW_LIST, direct), (PROXY_LIST, proxy)):
        lines, by_type = render(rules)
        texts[name] = header(name, stamp, len(lines)) + "\n".join(lines) + "\n"
        counts[name] = dict(by_type)

    return texts, counts


def read_text(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def atomic_write(path: str, text: str) -> None:
    """先写临时文件再改名，避免构建中断留下半个文件被 Pages 发布出去。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def too_broad(rtype: str, value: str) -> str:
    """返回过宽原因，正常返回空串。用来挡住 DOMAIN-SUFFIX,com 这类灾难。"""
    if rtype == "DOMAIN-SUFFIX" and "." not in value.strip("."):
        return f"单标签后缀 {value}（会匹配整个顶级域）"
    if rtype == "DOMAIN-KEYWORD" and len(value) < 3:
        return f"关键词过短 {value}（命中面过大）"
    return ""


def validate(texts, counts, min_ad=MIN_EXPECTED_AD, prev=None):
    """
    产物自检。任何一项不过就直接让 CI 失败，不要发布坏规则。

    prev: 上一次 meta.json 的内容（dict），用于漂移检测；None 则跳过。
    """
    problems = []

    ad_n = sum(counts[AD_LIST].values())
    allow_n = sum(counts[ALLOW_LIST].values())
    proxy_n = sum(counts[PROXY_LIST].values())

    # --- 数量与漂移
    if ad_n < min_ad:
        problems.append(f"广告规则只有 {ad_n} 条，低于阈值 {min_ad}")
    if allow_n == 0 and proxy_n == 0:
        problems.append("白名单与代理表同时为空，上游结构可能已变")
    if prev:
        prev_n = prev.get("ad_rules", 0)
        if prev_n:
            drift = abs(ad_n - prev_n) / prev_n
            if drift > MAX_DRIFT:
                problems.append(
                    f"广告规则数从 {prev_n} 变到 {ad_n}（波动 {drift:.0%}），"
                    f"超过 {MAX_DRIFT:.0%} 阈值"
                )

    # --- 逐行格式 + 过宽规则 + IP 泄漏
    for name, text in texts.items():
        for line in text.splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if s.count(",") != 1:
                problems.append(f"{name}: 字段数不对 -> {s}")
                continue
            rtype, value = [x.strip() for x in s.split(",")]
            if rtype not in TYPE_ORDER:
                if rtype in IP_TYPES:
                    problems.append(f"{name}: 规则集里不允许出现 IP 规则 -> {s}")
                else:
                    problems.append(f"{name}: 未知类型 -> {s}")
                continue
            reason = too_broad(rtype, value)
            if reason:
                problems.append(f"{name}: {reason}")

    # --- 防泄漏：自检站点必须走代理
    proxy_text = texts[PROXY_LIST]
    missing = [
        d for d in LEAK_TEST_DOMAINS
        if f"DOMAIN-SUFFIX,{d}" not in proxy_text and f"DOMAIN,{d}" not in proxy_text
    ]
    if missing:
        problems.append(f"泄漏自检域名未走代理（否则测泄漏必报本地 DNS）: {missing}")

    # --- 关键条目存在性
    for name, keys in MUST_HAVE.items():
        for k in keys:
            if k not in texts[name]:
                problems.append(f"{name}: 关键条目丢失 -> {k}")

    if problems:
        print("\n[校验失败]", file=sys.stderr)
        for p in problems:
            print("  -", p, file=sys.stderr)
        raise SystemExit(1)

    print(f"[6/6] 校验通过  广告 {ad_n} / 直连 {allow_n} / 代理 {proxy_n}")


def check_conf(conf_path: str):
    """
    检查主配置：DNS 设置 + 规则顺序 + IP 规则是否带 no-resolve。
    """
    if not os.path.exists(conf_path):
        print(f"跳过：{conf_path} 不存在")
        return

    text = read_text(conf_path)
    lines = text.splitlines()
    problems = []

    for k in ("[General]", "[Proxy Group]", "[Rule]", "FINAL,"):
        if k not in text:
            problems.append(f"缺少必要内容 {k}")

    # DNS 防泄漏基线
    if re.search(r"^dns-server\s*=\s*system\s*$", text, re.M):
        problems.append("dns-server 为 system（会走运营商 DNS），应改为 DoH")
    for key in ("dns-fallback-system", "dns-direct-system"):
        if not re.search(rf"^{key}\s*=\s*false\s*$", text, re.M):
            problems.append(f"{key} 应为 false")

    # IP 规则一律 no-resolve，且必须在「IP 规则」分节之后
    marker_idx = next(
        (i for i, l in enumerate(lines) if l.strip().startswith("# >> IP 规则")), None
    )
    for i, line in enumerate(lines, 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith(("IP-CIDR,", "IP-CIDR6,", "GEOIP,")):
            if "no-resolve" not in s:
                problems.append(f"L{i}: IP 规则缺 no-resolve -> {s}")
            if marker_idx is not None and i - 1 < marker_idx:
                problems.append(f"L{i}: IP 规则出现在域名规则之前 -> {s}")

    # 规则集里不许引用 IP 表（客户端会为匹配而提前解析域名）
    # 只在生效行里查，注释里提一句不算
    for i, line in enumerate(lines, 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if "ChinaIp.list" in s or "ChinaCompanyIp.list" in s:
            problems.append(f"L{i}: 主配置仍引用 IP 表（客户端会提前解析域名）-> {s}")

    print(f"[check] {conf_path}: {len(lines)} 行, {len(text.encode()) / 1024:.1f} KB")
    if problems:
        print("[check 失败]", file=sys.stderr)
        for p in problems:
            print("  -", p, file=sys.stderr)
        raise SystemExit(1)
    print("[check] 结构完整、DNS 配置与规则顺序合规")


def update_readme(meta: dict, readme_path: str = README_FILE) -> None:
    """把条数写回 README 的标记区间，避免文档数字漂移。"""
    if not os.path.exists(readme_path):
        print(f"跳过：{readme_path} 不存在")
        return

    text = read_text(readme_path)
    if README_START not in text or README_END not in text:
        print(f"跳过：{readme_path} 缺少 {README_START} / {README_END} 标记")
        return

    def n(v):
        return f"{v:,}"

    table = "\n".join([
        README_START,
        "| 文件 | 内容 | 条数 |",
        "|---|---|---:|",
        f"| `a-nomad.conf` | 主配置（订阅这个） | — |",
        f"| `unban.list` | 白名单 / 上游 Direct 类 | {n(meta['allow_rules'])} |",
        f"| `proxy.list` | 上游 Proxy 类 | {n(meta['proxy_rules'])} |",
        f"| `ad.list` | 广告域名 | {n(meta['ad_rules'])} |",
        "",
        f"数据来源：上游 `{meta['source_build_time']}`，"
        f"本地解析行 {n(meta.get('dropped', 0))} 行已丢弃。",
        README_END,
    ])

    start = text.index(README_START)
    end = text.index(README_END) + len(README_END)
    new = text[:start] + table + text[end:]
    if new != text:
        atomic_write(readme_path, new)
        print(f"[readme] 已写回条数到 {readme_path}")
    else:
        print("[readme] 无变化")


def expand_inline(conf_path: str, texts: dict, out_path: str) -> None:
    """
    生成单文件版：把三张表的内容原地展开进 .conf。
    给不想依赖远程规则集的人用。
    """
    policy = {AD_LIST: "🛑 广告拦截", ALLOW_LIST: "🎯 全球直连", PROXY_LIST: "🚀 节点选择"}
    marker_map = {name: f"/Shadowrocket-Ad-DNS-Leak-Rules/{name}" for name in policy}

    out = []
    for line in read_text(conf_path).splitlines():
        hit = next((name for name, m in marker_map.items() if m in line), None)
        if hit:
            out.append(f"# >> 内联展开：{hit}（{policy[hit]}）")
            body = [l for l in texts[hit].splitlines() if l and not l.startswith("#")]
            out.extend(f"{l}, {policy[hit]}" for l in body)
        else:
            out.append(line)

    atomic_write(out_path, "\n".join(out) + "\n")
    size = os.path.getsize(out_path) / 1048576
    print(f"[inline] 已生成 {out_path}  {size:.2f} MB")


def emit_ip(ip_rules: dict) -> None:
    """打印可直接粘贴进 conf 的 IP 规则（带 no-resolve）。"""
    if not ip_rules:
        print("（上游本轮没有 IP 规则）")
        return
    print("# 复制到你 conf 的「>> IP 规则」分节，必须带 no-resolve")
    for (rtype, value), policy in sorted(ip_rules.items()):
        target = (
            "🛑 广告拦截" if policy in REJECT_POLICIES
            else "🚀 节点选择" if policy in PROXY_POLICIES
            else "🎯 全球直连"
        )
        print(f"{rtype},{value},{target},no-resolve")


# ---------------------------------------------------------------- 主流程

def main() -> None:
    ap = argparse.ArgumentParser(description="构建 Shadowrocket 规则集")
    ap.add_argument("--url", action="store_true", help="先从上游下载最新 sr_cnip_ad.conf")
    ap.add_argument("--src", default=SRC_FILE, help="源配置路径")
    ap.add_argument("--conf", default=CONF_FILE, help="主配置路径")
    ap.add_argument("--inline", action="store_true", help="额外生成单文件版配置")
    ap.add_argument("--check", action="store_true", help="只校验，不构建")
    ap.add_argument("--update-readme", action="store_true", help="把条数写回 README")
    ap.add_argument("--emit-ip", action="store_true", help="打印 IP 规则（带 no-resolve）")
    ap.add_argument("--min-ad", type=int, default=MIN_EXPECTED_AD,
                    help=f"广告规则数下限（默认 {MIN_EXPECTED_AD}，本地试跑可设 1）")
    args = ap.parse_args()

    if args.check:
        counts = {}
        for path in (AD_LIST, ALLOW_LIST, PROXY_LIST):
            if not os.path.exists(path):
                raise SystemExit(f"[check] 缺少产物 {path}")
            by_type = defaultdict(int)
            for line in read_text(path).splitlines():
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                by_type[s.split(",")[0]] += 1
            counts[path] = dict(by_type)
            print(f"[check] {path}: {sum(by_type.values())} 条  {dict(by_type)}")

        meta = {}
        if os.path.exists(META_FILE):
            meta = json.loads(read_text(META_FILE))
        if args.emit_ip:
            print("\n# 提示：IP 规则不在产物里，需要 --url 构建时打印")
        validate(counts=counts, texts={
            p: read_text(p) for p in counts
        }, min_ad=args.min_ad, prev=None)
        check_conf(args.conf)
        if args.update_readme and meta:
            update_readme(meta)
        return

    if args.update_readme:
        meta = json.loads(read_text(META_FILE))
        update_readme(meta)
        return

    if args.url:
        fetch_upstream(UPSTREAM_URL, args.src)
    elif not os.path.exists(args.src):
        raise SystemExit(f"找不到 {args.src}，加 --url 从上游下载")

    print(f"[2/6] 解析 {args.src}")
    stamp = read_source_build_time(args.src)
    print(f"      上游构建时间: {stamp}")
    ad, direct, proxy, ip_rules, stats = load_rules(args.src)
    print(
        f"      原始 {stats['total']} 行 | 丢弃非法 {stats['dropped']} "
        f"| 未知策略 {stats['unknown_policy']} | 重复 {stats['dup']}"
    )
    print(
        f"      拦截 {len(ad)} | 直连 {len(direct)} | 代理 {len(proxy)} "
        f"| IP 规则 {len(ip_rules)}（不写入 .list）"
    )

    print("[3/6] 生成规则集")
    texts, counts = build_outputs(ad, direct, proxy, stamp)

    prev = None
    if os.path.exists(META_FILE):
        try:
            prev = json.loads(read_text(META_FILE))
        except Exception:
            prev = None

    print("[4/6] 写入产物")
    for name, text in texts.items():
        atomic_write(name, text)

    meta = {
        "source_build_time": stamp,
        "ad_rules": sum(counts[AD_LIST].values()),
        "allow_rules": sum(counts[ALLOW_LIST].values()),
        "proxy_rules": sum(counts[PROXY_LIST].values()),
        "by_type": counts,
        "ip_rules_excluded": len(ip_rules),
        "source_lines": stats["total"],
        "dropped": stats["dropped"],
        "unknown_policy": stats["unknown_policy"],
    }
    atomic_write(META_FILE, json.dumps(meta, ensure_ascii=False, indent=2) + "\n")

    print("[5/6] 自检")
    validate(texts, counts, min_ad=args.min_ad, prev=prev)
    check_conf(args.conf)

    if args.emit_ip:
        print()
        emit_ip(ip_rules)

    if args.inline:
        expand_inline(args.conf, texts, INLINE_OUT)

    print("\n完成。")


if __name__ == "__main__":
    main()
