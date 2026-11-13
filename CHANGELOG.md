# 变更记录

## v2.1.0 — 2026-11-14

修三个「会实际影响使用」的问题，并给浏览器加上防退化保护。

### 订阅者需要做什么

更新订阅即可，订阅地址不变：

```
https://nznl31.github.io/Shadowrocket-Ad-DNS-Leak-Rules/a-nomad.conf
```

### 修了什么问题

**1. 上游 Proxy 规则被整表改成了直连（严重）**

v2.0.0 的 `build_rules.py` 把上游的 `Proxy` 和 `Direct` 都归进同一个 `allow` 桶，输出规则行时不带策略，而 `a-nomad.conf` 又给整张 `unban.list` 配了「🎯 全球直连」。结果是上游明确要求走代理的约 370 条规则——Telegram、Disney+、Prime Video、Amazon / AWS、OneDrive、`raw.githubusercontent.com`、DNS 泄漏检测站点——全部变直连。

现在按策略分三桶：`ad.list`（Reject）、`proxy.list`（Proxy，新增）、`unban.list`（Direct）。

**2. 规则集里的 IP 规则导致 DNS 泄漏**

`unban.list`、`ad.list` 里混着 145 条 `IP-CIDR`，且挂在规则链最前面。客户端判断一条域名连接是否命中 IP 规则时必须先本地解析，于是每次上网都在向运营商暴露域名——这也解释了 issue #2 里「browserleaks 出现中国旗子」的报告。

现在 IP 规则不进规则集，只保留配置末尾的 `GEOIP,CN,...,no-resolve`；需要精确 IP 段时用 `build_rules.py --emit-ip` 打印带 `no-resolve` 的版本手动加入。同时移除对 `ChinaIp.list`、`ChinaCompanyIp.list` 的引用（纯 IP 表，同样会触发提前解析）。

**3. 泄漏自检站点本身是直连的**

`browserleaks.com`、`browserleaks.org`、`dnsleaktest.com`、`ipleak.net` 在上游是 `Proxy`，被上述问题一带就变成了直连——用它们测泄漏，测的其实是直连链路，永远显示本地 DNS。现在这四个域名显式走代理，并加了 CI 断言防止回退。

### 顺手修的

- `dns-server` 从 `system`（运营商 DNS）改回 DoH：`https://223.5.5.5/dns-query, https://1.12.12.12/dns-query`，用 IP 字面量避免引导阶段的解析泄漏；`dns-fallback-system = false`、`dns-direct-system = false` 补齐。
- `DOMAIN`（精确域名）不再被改写成 `DOMAIN-SUFFIX`，避免一条精确规则波及全部子域。
- 广告侧的 `DOMAIN-KEYWORD` 不再被静默丢弃（v2.0.0 只对白名单侧生效）。
- 移除 `OR` 组合里裸的 `(DOMAIN-SUFFIX, ca)`——那会把整个 `.ca` 顶级域送美国节点。
- `tun-excluded-routes` 去重并补上 `127.0.0.0/8`。
- 自检增强：广告规则下限从 10,000 提到 20,000；新增过宽规则拦截（`DOMAIN-SUFFIX,com`、过短关键词）、关键条目存在性断言、IP 规则位置与 `no-resolve` 检查。
- README 的条数表格改由 CI 从 `meta.json` 写入，不再手写。
- 工作流：新增 `push` / `pull_request` 触发测试；`cancel-in-progress` 改 `false`（避免打断正在推送的运行）；加 `timeout-minutes`；失败时自动开 issue；不再提交 2.1 MB 的 `sr_cnip_ad.conf`。
- 新增 `tests/`，23 个用例覆盖本次修复的每一类回归。

### 需要手动做的事（仓库维护者）

- `git rm` 掉根目录 `“Other rules are available in the johnshall repository.”/`（12 MB）、`figure/-`、`figure/免费在线二维码生成器.url`。
- `git rm --cached sr_cnip_ad.conf` 并保持 `.gitignore` 生效（历史瘦身另行安排，会影响所有 fork）。
- `readme.md` 改名为 `README.md`。

### 兼容性

订阅地址、策略组名称、分流逻辑均未改变，客户端无需调整。

本文数字以实际构建产物为准：上游 `UTC 2026-11-12 23:02:32` 那次构建得到广告 52,659 条、代理 373 条、白名单 0 条（上游当前没有 `Direct` 规则），另有 145 条 IP 规则被移出规则集。CI 每天会把最新数字写回 README 的 `RULES` 标记区。

---

## v2.0.0 — 2026-10-24

配置与规则集分离，修复配置文件只增不减的问题。

### 订阅者需要做什么

**不需要做任何事。** 订阅地址不变：

```
https://nznl31.github.io/Shadowrocket-Ad-DNS-Leak-Rules/a-nomad.conf
```

下次在 Shadowrocket 里更新订阅即自动生效。

### Fork 者需要做什么

**请 Sync fork。** 否则你 fork 里的旧工作流会继续按旧逻辑运行，配置文件会继续膨胀。

### 修了什么问题

旧版 `merge_rules.py` 把 `a-nomad.conf` 同时当作输入和输出（`output_path = a_nomad_path`），每天的流程是「读昨天的配置 → 加上游新增的域名 → 写回」，只做并集、从不删除。

后果是上游已经删掉的广告域名永远留在配置里。到 2026-10-24 为止：

- 配置文件 6.85 MB，内联规则 181,363 条
- 其中 **120,521 条（66.5%）在上游已不存在**
- 体积从 2025-08 的 1.68 MB 单调增长到 7.18 MB

### 改了什么

| 项目 | 旧 | 新 |
|---|---:|---:|
| 配置文件 | 6.85 MB | 11.2 KB |
| 规则条数 | 181,363 | 60,984 |
| 僵尸规则 | 120,521 | 0 |
| 工作流 | 2 个（靠 cron 时间差串联） | 1 个 |

- 新增 `build_rules.py`，每天从上游**重新构建**，天然幂等，不再追加
- 广告域名拆到 `ad.list`，白名单拆到 `unban.list`，主配置只保留框架
- 白名单规则集排在广告规则之前，修复被父域 `Reject` 规则吞掉的问题
- 清洗行尾注释（上游存在 `DOMAIN-SUFFIX,ampproject.org,PROXY # Google AMP issue#237` 这类把注释写进策略字段的坏行）
- 补齐原先被丢弃的 `DOMAIN-KEYWORD`（amazon / aws）和 `IP-CIDR` 规则
- 构建后自检：规则数低于阈值或波动超过 40% 直接让 CI 失败
- 产物使用上游构建时间而非本地时间，规则无变化时跳过提交
- 合并两个工作流为一个，去掉靠 GitHub Pages 中转造成的缓存竞态
- 删除 `merge_rules.py` 与两个旧工作流

### 兼容性

订阅地址、策略组名称、分流逻辑均未改变，客户端无需任何调整。

如果遇到问题，可以回滚到旧版本：

```bash
git revert e8caee8 4a0bbb2
```

### 已知遗留

- 仓库根目录下的 `“Other rules are available in the johnshall repository.”/` 目录含约 12 MB 上游配置副本，暂未清理。（v2.1.0 计划清理）
- 仓库历史约 215 MB（900+ 次提交累积），如需瘦身需重写 Git 历史，会影响已有 fork。
