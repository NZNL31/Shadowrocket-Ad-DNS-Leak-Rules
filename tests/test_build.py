#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_rules.py 的回归测试。

用标准库 unittest 写，不依赖 pytest（`python -m unittest` 和 `pytest` 都能跑）。
每个用例对应一个真实踩过的坑，注释里写了它防的是哪一类事故。
"""

import contextlib
import json  # noqa: F401  （保留给将来读 meta.json 的用例）
import shutil
import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import build_rules as br  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "upstream_sample.conf"
STAMP = "UTC 2026-11-12 23:02:32"


@contextlib.contextmanager
def scratch():
    """工作区内的临时目录（不用系统 temp，避免沙箱清理权限问题）。"""
    p = ROOT / "tests" / f".scratch-{uuid.uuid4().hex[:8]}"
    p.mkdir(parents=True, exist_ok=True)
    try:
        yield p
    finally:
        shutil.rmtree(p, ignore_errors=True)


def build_from_fixture():
    ad, direct, proxy, ip_rules, stats = br.load_rules(str(FIXTURE))
    texts, counts = br.build_outputs(ad, direct, proxy, STAMP)
    return ad, direct, proxy, ip_rules, stats, texts, counts


def body_lines(text):
    return [l for l in text.splitlines() if l.strip() and not l.startswith("#")]


class ParseRuleTest(unittest.TestCase):
    def test_strips_trailing_comment(self):
        """上游有 `...,PROXY # Google AMP issue#237`，策略字段不能被污染。"""
        got = br.parse_rule("DOMAIN-SUFFIX,ampproject.org,PROXY # Google AMP issue#237")
        self.assertEqual(got, ("DOMAIN-SUFFIX", "ampproject.org", "proxy"))

    def test_handles_no_resolve_option(self):
        got = br.parse_rule("IP-CIDR,1.2.3.0/24,Reject,no-resolve")
        self.assertEqual(got, ("IP-CIDR", "1.2.3.0/24", "reject"))

    def test_rejects_single_label_suffix(self):
        """防 DOMAIN-SUFFIX,com 这种会杀掉整个顶级域的规则。"""
        self.assertIsNone(br.parse_rule("DOMAIN-SUFFIX,com,Reject"))

    def test_ignores_section_header_and_garbage(self):
        self.assertIsNone(br.parse_rule("[Rule]"))
        self.assertIsNone(br.parse_rule("# 注释"))
        self.assertIsNone(br.parse_rule("GEOIP,CN,DIRECT"))


class LoadRulesTest(unittest.TestCase):
    def test_three_buckets_split_by_policy(self):
        """核心回归：Proxy 不能和 Direct 混在一张表里。"""
        _, direct, proxy, _, _, _, _ = build_from_fixture()
        self.assertIn(("DOMAIN-SUFFIX", "telegram.org"), proxy)
        self.assertIn(("DOMAIN-SUFFIX", "cn.example.com"), direct)
        self.assertNotIn(("DOMAIN-SUFFIX", "telegram.org"), direct)
        self.assertNotIn(("DOMAIN-SUFFIX", "cn.example.com"), proxy)

    def test_ip_rules_collected_separately(self):
        _, _, _, ip_rules, _, texts, _ = build_from_fixture()
        self.assertIn(("IP-CIDR", "91.108.4.0/22"), ip_rules)
        for name, text in texts.items():
            self.assertFalse(
                [l for l in body_lines(text) if l.startswith(("IP-CIDR,", "IP-CIDR6,"))],
                f"{name} 里不允许出现 IP 规则",
            )

    def test_stats_count_dropped_and_duplicates(self):
        _, _, _, _, stats, _, _ = build_from_fixture()
        self.assertGreaterEqual(stats["dup"], 1)
        self.assertGreaterEqual(stats["dropped"], 2)  # 单标签域 + GEOIP
        self.assertGreaterEqual(stats["unknown_policy"], 1)


class BuildOutputsTest(unittest.TestCase):
    def test_exact_domain_type_preserved(self):
        """DOMAIN（精确）不能被写成 DOMAIN-SUFFIX，否则波及全部子域。"""
        _, _, _, _, _, texts, _ = build_from_fixture()
        proxy_text = texts[br.PROXY_LIST]
        self.assertIn("DOMAIN,exact.example.com", proxy_text)
        self.assertNotIn("DOMAIN-SUFFIX,exact.example.com", proxy_text)

    def test_ad_keyword_is_emitted(self):
        """上游 Reject 分组里的 DOMAIN-KEYWORD 以前会被静默丢掉。"""
        _, _, _, _, _, texts, _ = build_from_fixture()
        self.assertIn("DOMAIN-KEYWORD,adservice", texts[br.AD_LIST])

    def test_header_carries_upstream_stamp(self):
        _, _, _, _, _, texts, _ = build_from_fixture()
        for text in texts.values():
            self.assertIn(STAMP, text)

    def test_counts_match_line_count(self):
        _, _, _, _, _, texts, counts = build_from_fixture()
        for name, text in texts.items():
            self.assertEqual(len(body_lines(text)), sum(counts[name].values()), name)


class ValidateTest(unittest.TestCase):
    def _counts(self, texts):
        out = {}
        for name, text in texts.items():
            by_type = {}
            for line in body_lines(text):
                t = line.split(",")[0]
                by_type[t] = by_type.get(t, 0) + 1
            out[name] = by_type
        return out

    def _good(self):
        _, _, _, _, _, texts, _ = build_from_fixture()
        return texts, self._counts(texts)

    def test_passes_on_fixture_output(self):
        texts, counts = self._good()
        br.validate(texts, counts, min_ad=1)  # 通过即不抛异常

    def test_rejects_broad_suffix(self):
        texts, counts = self._good()
        texts[br.AD_LIST] += "DOMAIN-SUFFIX,io\n"
        counts[br.AD_LIST]["DOMAIN-SUFFIX"] += 1
        with self.assertRaises(SystemExit):
            br.validate(texts, counts, min_ad=1)

    def test_rejects_short_keyword(self):
        texts, counts = self._good()
        texts[br.AD_LIST] += "DOMAIN-KEYWORD,ad\n"
        counts[br.AD_LIST]["DOMAIN-KEYWORD"] += 1
        with self.assertRaises(SystemExit):
            br.validate(texts, counts, min_ad=1)

    def test_allows_three_char_keyword(self):
        """上游真实存在 DOMAIN-KEYWORD,aws —— 3 字符是合理粒度，不该被拦。"""
        texts, counts = self._good()
        texts[br.PROXY_LIST] += "DOMAIN-KEYWORD,aws\n"
        counts[br.PROXY_LIST]["DOMAIN-KEYWORD"] += 1
        br.validate(texts, counts, min_ad=1)

    def test_rejects_ip_rule_in_list(self):
        """IP 规则进规则集 = 强制本地解析 = DNS 泄漏。"""
        texts, counts = self._good()
        texts[br.AD_LIST] += "IP-CIDR,1.2.3.0/24\n"
        counts[br.AD_LIST]["IP-CIDR"] = 1
        with self.assertRaises(SystemExit):
            br.validate(texts, counts, min_ad=1)

    def test_rejects_when_leak_test_domain_direct(self):
        """browserleaks 直连时，测泄漏永远显示本地 DNS。"""
        texts, counts = self._good()
        texts[br.PROXY_LIST] = "\n".join(
            l for l in texts[br.PROXY_LIST].splitlines()
            if "browserleaks.com" not in l
        ) + "\n"
        with self.assertRaises(SystemExit):
            br.validate(texts, counts, min_ad=1)

    def test_rejects_rule_count_drop(self):
        texts, counts = self._good()
        with self.assertRaises(SystemExit):
            br.validate(texts, counts, min_ad=1, prev={"ad_rules": 10_000})


class CheckConfTest(unittest.TestCase):
    """针对主配置的静态检查：DNS 设置 + 规则顺序 + no-resolve。"""

    def _write(self, d, replace=None):
        base = (ROOT / "a-nomad.conf").read_text(encoding="utf-8")
        for old, new in (replace or []):
            self.assertIn(old, base, "替换锚点不存在，配置被改过？")
            base = base.replace(old, new)
        p = Path(d) / "conf.conf"
        p.write_text(base, encoding="utf-8")
        return str(p)

    def test_repo_conf_passes(self):
        br.check_conf(str(ROOT / "a-nomad.conf"))

    def test_flags_system_dns(self):
        with scratch() as d:
            conf = self._write(d, [
                ("dns-server = https://223.5.5.5/dns-query, https://1.12.12.12/dns-query",
                 "dns-server = system"),
            ])
            with self.assertRaises(SystemExit):
                br.check_conf(conf)

    def test_flags_ip_rule_without_no_resolve(self):
        with scratch() as d:
            conf = self._write(d, [
                ("GEOIP, CN, 🎯 全球直连, no-resolve", "GEOIP, CN, 🎯 全球直连"),
            ])
            with self.assertRaises(SystemExit):
                br.check_conf(conf)

    def test_flags_ip_list_reference(self):
        with scratch() as d:
            conf = self._write(d, [(
                "# >> IP 规则（必须放在所有域名规则之后，且逐条带 no-resolve）",
                "RULE-SET,https://x/ChinaIp.list, 🎯 全球直连\n"
                "# >> IP 规则（必须放在所有域名规则之后，且逐条带 no-resolve）",
            )])
            with self.assertRaises(SystemExit):
                br.check_conf(conf)


class UpdateReadmeTest(unittest.TestCase):
    def test_writes_counts_between_markers(self):
        meta = {
            "source_build_time": STAMP,
            "ad_rules": 52_782,
            "allow_rules": 390,
            "proxy_rules": 371,
            "dropped": 2,
        }
        with scratch() as d:
            p = Path(d) / "README.md"
            p.write_text(
                f"前文\n{br.README_START}\n旧表格\n{br.README_END}\n后文\n",
                encoding="utf-8",
            )
            br.update_readme(meta, str(p))
            text = p.read_text(encoding="utf-8")
            self.assertIn("52,782", text)
            self.assertIn("371", text)
            self.assertNotIn("旧表格", text)
            self.assertTrue(text.startswith("前文"))
            self.assertTrue(text.strip().endswith("后文"))

    def test_no_marker_is_noop(self):
        with scratch() as d:
            p = Path(d) / "README.md"
            p.write_text("没有标记\n", encoding="utf-8")
            br.update_readme({"source_build_time": STAMP, "ad_rules": 1,
                              "allow_rules": 1, "proxy_rules": 1}, str(p))
            self.assertEqual(p.read_text(encoding="utf-8"), "没有标记\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
