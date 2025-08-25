## 最完善的 iOS Shadowrocket规则
### 修改说明：摆脱广告困扰的同时，解决Shadowrocket在配置模式下，可能出现的DNS泄露问题。
基于 Shadowrocket-ADBlock-Rules-Forever 项目，结合 colin-chang 设计的 a-nomad.conf 进行个人优化。原版 a-nomad.conf 以防 DNS 泄露为目的，引用了 ACL4SSR 的分流规则。**规则会在每天北京时间 10:00 自动更新。**

规则地址：<https://nznl31.github.io/Shadowrocket-Ad-DNS-Leak-Rules/a-nomad.conf>

![二维码](https://nznl31.github.io/Shadowrocket-Ad-DNS-Leak-Rules/figure/a-nomad.png)

以原版 a-nomad.conf 为基础，**仅**引入原项目每日更新的**国内外划分 + 广告**规则，旨在提升 DNS 泄露防护与广告屏蔽效果，兼顾安全与隐私。作为初学者，虚心求教，欢迎监督和建议。感谢 Shadowrocket-ADBlock-Rules-Forever 项目原作者及维护者，colin-chang 以及所有规则维护者，感谢 ChatGPT 在规则合并和脚本编写中的帮助。
