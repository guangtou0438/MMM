# 重构实施与验收记录

日期：2026-09-06。依据：`AGENT_MEMORY_REFACTOR_SPEC_ZH.md`。本文件描述代码已有能力，不替换主规格。

## 已落地的主链路

- 原生 JavaScript 的日历、Agent 对话、记忆中心三个入口；旧聊天表单转入新 Run。
- SQLite 持久 Run/队列/事件/工具审计，SSE 游标续读，等待后恢复同一 Run，重启后的确认恢复。
- 1/2/6 次模型调用、Token/时间/工具输出限制、重复调用和连续失败停止、两个模型槽位、同 Session 写 Run 互斥。
- 严格 Tool Schema、Skill 数据和网络权限、日历幂等与版本校验、原子流程补偿。永久删除仅在明确 UI 确认 API，未注册为 Tool。
- Working Memory、短期 TTL、五类长期记忆、来源、置信度、版本、访问计数、关系、纠正、锁定、忘记和重新处理。
- 真实本地多语言 Embedding、FTS5、sqlite-vec 相似度召回和代码重排，状态可查询，失败不伪造命中。
- 事件巩固队列、结构化 IR、高风险独立校验、保留策略、七天原文清理及哈希/lineage 保留。
- Open-Meteo Provider、摘要缓存与响应哈希、代码天气阈值、08:00 后台检查、建议去重、前台接受改期。
- PDF/ICS/CSV 字段白名单导出、CSV 公式转义、ICS 折行、PDF 中文字体、仅元数据审计。
- 天气程序候选、成功 Run 来源、确定性回放、用户启用门槛。
- A/B/C 合成评测及 GLM/DeepSeek 实际调用，四份 Archify 文档与讲解稿。

## 验证证据

`scripts/test_current.py` 共 74 项回归全部通过，覆盖原有功能与新增 Agent 回归。新增测试验证运行时互斥、断线重放、重启、原 Run 恢复、取消后的迟到写入、预算、防永久删除、补偿、来源、锁定、派生记忆忘记、高风险门、保留、真实 sqlite-vec 扩展、重建失败保留旧索引、天气去重和导出无落盘。

`artifacts/refactor-ui-check.json` 记录合成浏览器测试，截图仅包含虚构用户。`artifacts/refactor-export-preview.png` 是合成 PDF 渲染结果；临时 PDF 已移除。

`artifacts/evaluation-synthetic-live.json` 保存真实双模型结果，`mode=live`；fixture 文件只验证数据流。每组八题，结果包含回答与失败证据。C 组额外记录实际巩固过程的状态、模型调用、Token 和耗时。

最终综合基准为 `artifacts/evaluation-suite-live.json`，可读表格为同名 `.md`。其中保留了 GLM 将数字日历 ID 写入记忆引用字段造成的原始失败。协议层现已丢弃数字伪引用，字符串引用仍须来自有效召回；`artifacts/refactor-protocol-live.json` 记录修复后的真实重测：任务成功创建、2 次调用、4197 Token。该重测没有覆盖或抹去原基准失败。

DeepSeek 的独立校验请求原先未明确包含 JSON 格式指令，导致 C 组四个高风险案例在校验阶段被接口拒绝。生成器和重排/校验提示现已统一明确 JSON；`artifacts/refactor-consolidation-live.json` 的常住地和截止日期针对性重测均完成生成与独立校验，每题 2 次真实调用，分别 1611、1833 Token。完整基准没有重新计分为修复后的成绩。

基准原始结果：GLM A/B/C 成功率为 12.5%/75%/100%，DeepSeek 为 0%/50%/50%。已忘记记忆误召回均为 0。天气启用前两模型均 3/3 完成；启用后 GLM 3/3、DeepSeek 1/3，其余为预算停止和等待用户，均未误改日历。程序候选的质量提升尚未得到该小样本证明，保持默认关闭，由用户选择。

四份图的 `.receipt.json` 均通过 9 项 showcase 检查，0 错误、0 警告。Windows 上 Archify 原生 DevTools pipe 以 exit code 3 失败，因此其原生 `visual-check` 不能标为成功。`*.supplementary-browser.json` 是独立 Playwright 浏览器证据，检查了 1440×900、1600×1000、1920×1080、2048×1320 的容纳情况并保存深浅主题截图。图和 PDF 已进行图像检查。

## 评测边界

综合评测入口为 `python -m calendar_agent.evaluation.suite --live --semantic`，包含 A/B/C 问答、引用与巩固保持、工具工作流和天气候选启用前后的模型回放。所有输入均为合成数据，真实调用结果与 fixture 结果分开保存。

- 程序候选的应用内启用门检查确定性天气策略；综合基准另行运行端到端模型回放，不能把确定性门的 100% 当作模型成功率。
- 每个模型每个记忆组仅八题，天气回放每阶段三种预报。样本适合演示与回归，不构成统计上的普遍性能结论。
- 原生 Archify `visual-check` 在本机仍有上述工具兼容限制，保留失败记录和独立浏览器补充证据。

实际模型错误、预算耗尽、没有引用和候选未达成功门槛均原样保留，不能用 fixture 分数替代。
