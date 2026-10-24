> [!IMPORTANT]
> **2026-10-24 架构调整（v2.0.0）**：配置文件从 **6.85 MB 降到 11 KB**，规则改为独立规则集按需拉取。
>
> - **只是订阅使用的朋友：什么都不用做。** 订阅地址不变，下次更新订阅自动生效。
> - **Fork 过本仓库的朋友：请点一次 [Sync fork](https://docs.github.com/pull-requests/collaborating-with-pull-requests/working-with-forks/syncing-a-fork)。** 你 fork 里的旧工作流仍在按旧逻辑运行，会让配置继续膨胀到 7 MB 以上。
>
> 变更详情见 [CHANGELOG.md](CHANGELOG.md)。

## 摆脱广告困扰的同时，解决 Shadowrocket 在配置模式下可能出现的 DNS 泄露问题

基于 [Shadowrocket-ADBlock-Rules-Forever](https://github.com/Johnshall/Shadowrocket-ADBlock-Rules-Forever) 项目，结合 colin-chang 设计的 `a-nomad.conf` 进行个人优化。原版 `a-nomad.conf` 以防 DNS 泄露为目的，引用了 ACL4SSR 的分流规则。**规则每天北京时间 09:00 自动更新。**

规则地址：<https://nznl31.github.io/Shadowrocket-Ad-DNS-Leak-Rules/a-nomad.conf>

![二维码](https://nznl31.github.io/Shadowrocket-Ad-DNS-Leak-Rules/figure/a-nomad.png)

以原版 `a-nomad.conf` 为基础，引入原项目每日更新的**国内外划分 + 广告**规则，旨在提升 DNS 泄露防护与广告屏蔽效果，兼顾安全与隐私。作为初学者，虚心求教，欢迎监督和建议。感谢 Shadowrocket-ADBlock-Rules-Forever 项目原作者及维护者、colin-chang 以及所有规则维护者。

---

## 规则集结构

主配置 `a-nomad.conf` 只负责策略组和分流框架，体积约 11 KB，内容基本不变。每天变化的大批量域名放在独立规则集里，由 Shadowrocket 按 `update-interval` 增量拉取：

| 文件 | 内容 | 条数 | 说明 |
|---|---|---:|---|
| `a-nomad.conf` | 主配置 | — | 订阅这个地址即可，无需其他操作 |
| `unban.list` | 白名单 | 390 | 必须在广告规则**之前**匹配，否则会被父域拦截规则吞掉 |
| `ad.list` | 广告域名 | 60,594 | 每日重建 |

这样拆分的两个好处：订阅地址保持轻量、秒开；规则每天从上游**重建**而不是追加，不会像以前那样只增不减。

> 如果你更希望单文件、不依赖远程规则集，可以用 `python build_rules.py --inline` 生成 `a-nomad.full.conf`（约 3 MB）自行托管。

## 更新机制

`.github/workflows/update-rules.yml` 每天执行一次：

```
下载上游 sr_cnip_ad.conf → 构建 ad.list / unban.list → 校验 → 提交
```

构建脚本 `build_rules.py` 会做这些事：

- 从上游**重新构建**，不做增量追加（这是旧方案配置膨胀到 6.8 MB 的原因）
- 清洗脏数据：行尾注释、大小写、空格、非法字符
- 白名单独立成表并排在广告规则之前
- 保留 `DOMAIN-KEYWORD`、`IP-CIDR` 等非域名规则
- 产物自检：规则数低于阈值或波动超过 40% 直接让 CI 失败，不会发布坏规则

## 本地构建

```bash
python build_rules.py          # 用本地 sr_cnip_ad.conf 构建
python build_rules.py --url    # 先从上游下载最新版再构建
python build_rules.py --check  # 只校验产物
python build_rules.py --inline # 额外生成单文件版
```

## 如果你 fork 过这个仓库

Fork 不会自动同步上游改动。2026-10-24 的这次重构改变了构建方式，**不同步的话你的 fork 会继续按旧逻辑膨胀**。

两种处理方式：

**方式一：同步后继续用自己 fork 的地址**

在 fork 页面点 `Sync fork` → `Update branch`。同步后记得去 Actions 页面确认旧工作流（`Update Ad Rules Daily`、`更新广告规则并合并`）已被删除，新工作流 `构建广告规则集` 存在，然后手动跑一次。

**方式二：直接改用上游地址**

```
https://nznl31.github.io/Shadowrocket-Ad-DNS-Leak-Rules/a-nomad.conf
```

更省事，也能自动拿到后续更新。

## 变更记录

见 [CHANGELOG.md](CHANGELOG.md)。

想第一时间收到规则更新和架构变更通知，点右上角 **Watch → Custom → 勾选 Releases**（只收版本发布，不会刷屏）。
