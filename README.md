> [!IMPORTANT]
> **2026-11-14 架构调整（v2.1.0）**：修复三个会实际影响使用的问题。
>
> - **Telegram / Disney+ / Prime Video / Amazon 原本被误配成直连**，现已恢复走代理（新增 `proxy.list`）。
> - **规则集里的 IP 规则是 DNS 泄漏的根源**：客户端为匹配 IP 规则会提前解析域名。IP 规则已全部移出规则集，只保留在配置末尾并带 `no-resolve`。
> - **DNS 改回 DoH**（`223.5.5.5` / `1.12.12.12`，IP 字面量），不再使用系统 DNS。
>
> 订阅地址不变，更新订阅即生效。Fork 过的朋友请 Sync fork，否则你那边仍是旧逻辑。

## 摆脱广告困扰的同时，解决 Shadowrocket 在配置模式下可能出现的 DNS 泄露问题

基于 [Shadowrocket-ADBlock-Rules-Forever](https://github.com/Johnshall/Shadowrocket-ADBlock-Rules-Forever) 项目，结合 colin-chang 设计的 `a-nomad.conf` 进行个人优化。**规则每天北京时间 09:00 自动更新。**

规则地址：<https://nznl31.github.io/Shadowrocket-Ad-DNS-Leak-Rules/a-nomad.conf>

![二维码](https://nznl31.github.io/Shadowrocket-Ad-DNS-Leak-Rules/figure/a-nomad.png)

---

## 规则集结构

主配置 `a-nomad.conf` 只负责策略组、DNS 设置和分流框架，内容基本不变。每天变化的大批量域名放在独立规则集里，由 Shadowrocket 按 `update-interval` 增量拉取。

下面这张表由 CI 在每次构建后自动写入，数字不手写，避免文档漂移：

<!-- RULES:START -->
| 文件 | 内容 | 条数 |
|---|---|---:|
| `a-nomad.conf` | 主配置（订阅这个） | — |
| `unban.list` | 白名单 / 上游 Direct 类 | 0 |
| `proxy.list` | 上游 Proxy 类 | 373 |
| `ad.list` | 广告域名 | 53,072 |

数据来源：上游 `UTC 2026-11-13 23:02:50`，本地解析行 2 行已丢弃。
<!-- RULES:END -->

三张表的分工对应上游的三种策略，**不再混在一起**：

| 上游策略 | 落到哪张表 | 在配置里的策略 |
|---|---|---|
| `Reject` | `ad.list` | 🛑 广告拦截 |
| `Proxy` | `proxy.list` | 🚀 节点选择 |
| `Direct` | `unban.list` | 🎯 全球直连 |

> v2.0.0 把 `Proxy` 和 `Direct` 都塞进了同一张 `unban.list`，而整张表被配成「🎯 全球直连」——于是上游要求走代理的 Telegram、Disney+、Amazon、`raw.githubusercontent.com` 全部直连。v2.1.0 修掉了这个问题。

上游当前**没有 `Direct` 规则**（0 条），所以 `unban.list` 是空的；这张表保留着，上游哪天恢复 Direct 分类时会自动生效，不需要再改配置。

顺序上 `proxy.list` 排在 `ad.list` **之前**：上游把「手工定义的 Proxy 列表」放在 Reject 列表前面，其中像 `amazon-adsystem.com` 这样的域名同时命中广告规则，顺序反了就会改变上游的分流意图。

`proxy.list` 里的 `DOMAIN-KEYWORD,amazon`、`DOMAIN-KEYWORD,aws` 是上游的关键词级规则，命中面较宽（Amazon / AWS 全站长尾都走代理）。不想要的话删掉这两行即可。

## 为什么这样能防 DNS 泄漏

DNS 泄漏在 Shadowrocket 里几乎都来自同一个机制：**客户端为了判断某条域名规则是否命中 IP 规则，必须先把它解析成 IP。** 只要规则链里 IP 规则出现得早，几乎每条连接都会被提前本地解析一次。

所以这份配置守三条规矩：

1. **规则集里不允许有 IP 规则。** `ad.list` / `unban.list` / `proxy.list` 只含 `DOMAIN` / `DOMAIN-SUFFIX` / `DOMAIN-KEYWORD`。提交坏规则会被 CI 拦下。
2. **IP 规则一律放在所有域名规则之后，且逐条 `no-resolve`。** 目前只剩末尾一条 `GEOIP,CN,🎯 全球直连,no-resolve`。
3. **DNS 全部走 DoH，且不许回落系统。** `dns-server` 用 IP 字面量（避免「解析 DoH 域名」这一步本身泄漏），`dns-fallback-system = false`、`dns-direct-system = false`，并关闭 IPv6 防止 AAAA 绕过。

代价说明：不再引用 `ChinaIp.list` / `ChinaCompanyIp.list` 这两张纯 IP 表，少数「域名不在 ChinaDomain、服务器在国内」的站点会走代理。这是为了防泄漏做的取舍；想换回来就按配置里注释的格式加 `IP-CIDR` 规则，**务必带 `no-resolve`**。

## 更新机制

`.github/workflows/update-rules.yml` 每天执行一次：

```
单元测试 → 下载上游 sr_cnip_ad.conf → 构建三张表 → 自检 → 写回 README → 提交
```

构建脚本 `build_rules.py` 会做这些事：

- 从上游**重新构建**，不做增量追加（旧方案配置膨胀到 6.8 MB 的原因）
- 按策略分三桶，按规则类型原样输出（`DOMAIN` 不会被放大成 `DOMAIN-SUFFIX`）
- 清洗脏数据：行尾注释、大小写、空格、非法字符
- IP 规则单独提取，**不写进规则集**；需要时 `--emit-ip` 打印出带 `no-resolve` 的版本
- 产物自检：条数阈值、波动阈值、过宽规则（`DOMAIN-SUFFIX,com` 这种）、关键条目存在性、自检站点必须走代理
- 用上游构建时间做产物指纹，规则无变化时跳过提交

## 本地构建

```bash
python build_rules.py            # 用本地 sr_cnip_ad.conf 构建
python build_rules.py --url      # 先从上游下载最新版再构建
python build_rules.py --check    # 只校验产物 + 主配置
python build_rules.py --emit-ip  # 打印可粘贴的 IP 规则（带 no-resolve）
python build_rules.py --inline   # 额外生成单文件版 a-nomad.full.conf
python -m unittest discover -s tests -v   # 跑回归测试
```

## 常见问题

**Q：browserleaks 还是显示中国大陆的 DNS，怎么办？**

按顺序排查四条：

1. **更新订阅了吗？** 旧版配置里 `browserleaks.com` 等自检站点是直连的，测出来必然是中国 DNS。v2.1.0 已改为走代理。
2. **看日志，别看网页。** 在 Shadowrocket 的请求日志里打开 browserleaks，如果该连接显示的是**已解析 IP**而不是**域名**，说明还有 IP 规则在前面触发了解析。
3. **客户端是否内置了 DoH/DoT。** Chrome、部分国产 App 自带加密 DNS，会绕过系统解析，规则管不到。
4. **IPv6 是否关闭。** 开着 IPv6 时 AAAA 查询可能走运营商，表现为「偶发泄漏」。

**Q：某个网站被误杀了 / 本该走代理却直连了？**

先确认是哪张表命中：`ad.list`（拦截）、`proxy.list`（代理）、`unban.list`（直连）。个人例外直接写进 `a-nomad.conf` 的「手动覆盖」分节，它排在所有规则集之前。

**Q：Fork 过这个仓库怎么办？**

本次重构改变了构建方式和产物清单。请在 fork 页面点 **Sync fork → Update branch**，确认 Actions 里旧工作流（`Update Ad Rules Daily`、`更新广告规则并合并`）已被删除、新工作流 `构建广告规则集` 存在，然后手动跑一次。更省事的做法是直接改用上面的上游地址。

## 变更记录

见 [CHANGELOG.md](CHANGELOG.md)。

想第一时间收到规则更新和架构变更通知，点右上角 **Watch → Custom → 勾选 Releases**（只收版本发布，不会刷屏）。

## 致谢与许可

感谢 [Shadowrocket-ADBlock-Rules-Forever](https://github.com/Johnshall/Shadowrocket-ADBlock-Rules-Forever) 项目原作者 Moshel 与 Johnshall、[colin-chang](https://github.com/colin-chang) 的 `a-nomad.conf`，以及 ACL4SSR 等所有规则维护者。

- 规则数据（`*.list`）：CC BY-SA 4.0
- 构建脚本（`build_rules.py`、`tests/`）：MIT
- 上游项目的许可条款以其仓库为准；本仓库是衍生作品，二次分发请同时保留上游署名。
