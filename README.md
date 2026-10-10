# Calendar Memory Agent

一个本地、单用户的日历记忆 Agent。前端使用原生 JavaScript，后端使用 FastAPI、SQLite、FTS5 与 sqlite-vec。主规格见 [重构规格](docs/AGENT_MEMORY_REFACTOR_SPEC_ZH.md)，实现范围、验证证据和限制见 [验收记录](docs/REFACTOR_DELIVERY.md)。

## 启动

```powershell
py -3.12 -m venv .venv-calendar
.venv-calendar/Scripts/python.exe -m pip install -r requirements-calendar.lock.txt
.venv-calendar/Scripts/python.exe scripts/setup_embeddings.py --download
Copy-Item .env.example .env  # 仅首次配置；已有 .env 时不要覆盖
./calendar.ps1
```

浏览器打开 http://127.0.0.1:8765 。已有虚拟环境可直接使用 `./calendar.ps1`。

`.env` 分别配置 `ZHIPU_API_KEY` 和 `DEEPSEEK_API_KEY`，Agent 页手动选择 GLM-5.3-Flash 或 DeepSeek-V4-Flash。两者使用各自的 Key 和地址；失败回退记录在同一个 Run 中。`MMM_OFFLINE=1` 禁止真实 LLM 请求。模型缓存位于用户目录 `.cache/calendar-agent-models`，不在项目目录内。

`CALENDAR_DB_PATH` 指定数据库；默认 `data/calendar.db`。应用保留已识别的日历 Schema 的兼容入口，拒绝未知数据库和未知 Agent Schema。重构没有清空现有用户数据库。生产运行方式是一个本地进程；不要用多进程 worker 绕开进程内模型并发控制。

## 使用

1. **日历与安排**：创建、调整、完成、归档和恢复任务。日历旁的安排助手专注日程、活动与任务管理，支持：直接选择模型、流式回答、决策摘要、工具和记忆依据、取消运行及新对话。侧边栏“对话”按独立会话显示历史，可恢复仍保留的多轮记录并继续聊天；任务内聊天仍从对应日历任务进入。安排助手支持完整任务能力，无需选择任务范围。另有独立的「聊天」页面，用于日常交流、问答和讨论，不开放日历操作工具；两页使用不同的职责提示词、会话记录和输入草稿。首页同时显示天气建议和 PDF、ICS、CSV 导出。回收站中的永久删除需要明确 UI 确认。
2. **记忆中心**：查看五类长期记忆，明确添加、纠正、锁定、忘记和重新处理来源；管理常住地或带有效期的临时地点；更新语义索引；查看后台巩固任务和 Skill 开关。

户外规划会装配相关地点记忆。后台在 Melbourne 时间 08:00 检查次日户外活动，无活动时不调用天气接口。预报越过代码阈值后只创建建议，接受建议并选择日期才启动前台修改 Run。预报由 [Open-Meteo](https://open-meteo.com/en/docs) 提供，地理编码遵循其 [地点查询接口](https://open-meteo.com/en/docs/geocoding-api)。

## 工程边界

| 目录 | 职责 |
| --- | --- |
| `agent/` | SQLite 队列、Run 状态、事件、预算、恢复和调度 |
| `skills/` | 内置能力、工具白名单、数据/网络权限、扩展 manifest |
| `tools/` | 严格 Schema 的日历操作、天气建议、导出、补偿 |
| `memory/` | Working/短期/长期记忆、IR、来源、检索、巩固、清理 |
| `providers/` | LLM、Embedding、Weather 适配和安全协议规范化 |
| `evaluation/` | 合成数据、评分和 A/B/C 消融 |
| `api/agent.py` | Run/SSE、记忆中心、Skill、天气与下载 API |

日历事实仍由现有 `core/date_tasks.py` 统一写入。新的任务记忆保存语义背景与日历引用；它不会成为日期或状态的另一个事实来源。旧接口保留用于已有数据和回归兼容，当前可见聊天入口走新 Run 链路，旧后台记忆定时器已停用。

对话没有调用次数、累计 Token、运行时长或 Skill 调用次数预算上限。Token 和调用次数仅用于记录用量；用户可以取消运行。重复工具调用、连续失败、写入权限和版本校验仍由代码保护，供应商自身的上下文容量与网络超时仍适用。

每次日历写入有独立事务、幂等回执和版本校验。非原子流程后续失败保留已成功操作；显式 `atomic` Skill 才倒序执行服务端补偿，遇到用户介入造成的版本变化会报告补偿失败。

## 模型调用协议

当前 Agent 前台和后台记忆提取、独立校验使用 API 原生 Function Calling：字段 Schema 放在请求的 `tools[].function.parameters` 中，读取响应的 `tool_calls` 参数。前台按函数名生成内部决策，不要求模型手写 `action`；工具结果使用对应的 `tool_call_id` 回传，最终回答和澄清分别使用 `complete_answer`、`request_clarification`。

当前以两个供应商实测兼容的 `tool_choice: auto` 接入，未启用供应商 `strict` 模式，不承诺生成阶段必然符合所有字段约束。应用继续检查参数、来源、版本、权限和引用。单轮多个调用、未知函数、截断响应不会执行；后台生成协议错误最多重试一次。语义重排仍使用 JSON 模式；确定性测试替身保留旧接口兼容。

原生调用验证记录见 [Function Calling 验收](artifacts/function-calling-report.md)。

## 验证与评测

```powershell
.venv-calendar/Scripts/python.exe scripts/test_current.py
.venv-calendar/Scripts/python.exe -m calendar_agent.evaluation.suite --output artifacts/evaluation-suite-fixture.json
.venv-calendar/Scripts/python.exe -m calendar_agent.evaluation.suite --live --semantic --output artifacts/evaluation-suite-live.json
```

第一条运行确定性回归，不调用真实模型。第二条使用测试替身，只验证数据流。第三条会使用配置的两个模型与本地 Embedding，调用真实接口，所有输入来自脚本中的虚构用户，所有数据库位于临时目录。

A 为单次调用且不装配长期记忆，B 为 ReAct 加关键词召回，C 为 ReAct 加真实混合检索和模型记忆巩固。每组有 8 个中英文问题，并放入干扰记忆。C 组单独记录巩固调用、Token 和耗时；聊天 Token 不混入这笔后台成本。最终答案、错误及评分保存在结果文件中。样本用于工程演示，不足以证明某个模型普遍更好。

浏览器回归使用 `scripts/refactor_ui_server.py` 提供临时合成服务，`scripts/check_refactor_ui.cjs` 检查三个入口、刷新恢复、纠正、下载与响应式布局。

综合评测同时执行创建任务和天气程序启用前后的端到端回放，记录工具选择、成功率、误操作、步骤、Token 和延迟。天气输入固定为合成降雨、高温和大风，结果可以重复核对；候选由三次成功 Run 形成，模拟用户明确启用。记忆评测另记录引用覆盖、引用正确率和巩固后关键事实保持。没有调用或引用时对应指标保留空值。

已有结果见 [综合评测表](artifacts/evaluation-suite-live.md)。它保留修复前发现的协议失败；修复后针对性真实重测见 [验收记录](docs/REFACTOR_DELIVERY.md)。天气候选默认关闭，当前小样本没有证明它能普遍提高成功率。

语义索引支持后台构建新模型版本并原子切换，失败时旧索引继续服务；复杂任务遇到候选分数接近时，可做一次语义排序。可选扩展通过 `CALENDAR_SKILLS_DIR` 指向声明式 manifest 目录，默认关闭，不能加载可执行代码或自行扩大工具权限。

## 项目讲解

- [实现与验收记录](docs/REFACTOR_DELIVERY.md)
- [面试讲解与问答](docs/INTERVIEW_ZH.md)
- [系统架构](docs/diagrams/architecture.html)
- [记忆数据流](docs/diagrams/dataflow.html)
- [Run 生命周期](docs/diagrams/run-lifecycle.html)
- [记忆保留生命周期](docs/diagrams/memory-lifecycle.html)

四份图均附带 Archify 交付回执和浏览器检查记录。数据库、模型权重、凭证和用户导出文件不作为仓库内容保存。PDF 在内存生成并随响应释放，不产生项目中的临时 PDF 文件；中文字体优先使用系统宋体，也可用 `CALENDAR_PDF_FONT` 指定字体路径。
