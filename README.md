# 综合选股 · 七指标量化评分

一个为 AI 智能体（Agent）设计的股票筛选 Skill：用七个维度对 A 股候选池加权打分（100 分制），输出可排序、可追溯的深色金融终端风格 HTML 报告。

> 仅限学习研究参考，输出为数据筛选与量化评分，**不构成任何投资建议**。股市有风险，投资需谨慎。

## 它做什么

以市盈率（相对行业）、每股收益、行业地位、量能、换手率、市净率等七个指标加权评分；75 分以上优选、60~75 观察、60 以下剔除。含板块参数自适应、动态换手锚、公告一票否决、数据冲突仲裁、IC 跟踪等规则。

## 怎么用

把本目录作为 Skill 安装到你的智能体（如 WorkBuddy / Codex），然后直接说：

- 「帮我综合选股」
- 「在医药行业里筛几只值得关注的股票」
- 「按换手率 + 市盈率选股」

详细规则见 [`SKILL.md`](SKILL.md)。

## 数据来源（按优先级降级）

| 通道 | 依赖 |
|---|---|
| A | westockdata 技能 + 腾讯自选股连接器 |
| B | NeoData 连接器 |
| C | 通达信连接器 |
| D（兜底） | 东方财富网页选股器只读接口（免费、无需连接器） |

四通道全部不可用时，Skill 会提示先安装连接器，**不会编造行情数据**。

## 文件

- `SKILL.md` — 完整选股规则与评分口径
- `veto_check.py` — 公告一票否决校验辅助脚本
- `ic_tracker.py` — 评分有效性（IC）跟踪辅助脚本

## 许可

MIT © 2026 xq15591701012。可自由使用、修改、商用，请保留署名。

---

## English

A stock-screening Skill for AI agents. Scores A-share candidates on seven weighted factors (100-point scale) and outputs a sortable, self-contained dark-terminal HTML report. For learning and research only — **not investment advice**. Licensed under MIT.
