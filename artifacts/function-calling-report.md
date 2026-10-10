# 原生 Function Calling 接入与验证

日期：2026-09-15。测试均使用虚构数据和临时数据库；没有改动用户真实日历数据库。

## 改动

- Provider 将工具定义通过 `tools[].function.parameters` 提交，工具请求不再设置 `response_format: json_object`。当前使用两个供应商实测兼容的 `tool_choice: auto`，没有启用 strict 模式。
- 流式拼接函数名、参数和调用 ID；处理完成原因。未知函数、多函数响应、缺调用 ID、截断响应或非对象参数均被拒绝。普通正文不会被解析成工具调用。
- 后台提取使用 `propose_memory_operation`，独立校验使用 `verify_memory_operation`。Schema 通过 API 传递，不再拼入真实调用的提示词。
- 前台为每个已启用 Skill/Tool 提供独立函数。最终回答和澄清分别调用 `complete_answer` 与 `request_clarification`，模型不再手写内部 action/skill/tool 决策。
- 内部仍复用原来的授权执行器、幂等回执、版本控制和补偿机制。工具结果按原始调用 ID 使用 `role: tool` 回传；保留供应商多轮调用需要的内部字段，但不在用户事件中显示私有推理内容。
- 回答参数支持流式展示。来源、字段、引用和版本仍由本地验证；后台协议错误最多重试一次，不会无限调用。
- 语义重排保留 JSON 模式。仅提供旧接口的确定性测试替身仍可运行旧路径；真实 LLMProvider 没有自动退回“正文里解析工具命令”的兜底。

## 真实测试结果

| 场景 | GLM | DeepSeek |
|---|---|---|
| 三事实提取并独立校验 | 通过 | 通过 |
| 新会话回忆自行车、读书小组、棋类 | 三项正确 | 三项正确 |
| 创建一个指定日期任务 | 通过 | 通过 |
| 读取当前任务并按版本修改标题 | 通过 | 通过 |
| 名称和日期缺失时先询问，不自行创建 | 通过 | 通过 |

任务测试核对真实数据库状态：始终只有一条测试任务，标题由“原生调用验收”改成“原生调用已验证”；澄清回合未新增任务，Run 进入 waiting_for_user。

GLM 三个任务回合分别使用 2、3、1 次调用；DeepSeek 分别使用 3、3、1 次。请求记录包含真实原生函数名、返回的 tool_calls 分片、完成原因及下一轮携带的工具结果 ID。

记忆原文仍使用“自行车叫银杏、读书小组叫雾松、最喜欢围棋”。两个模型均完成后台两次函数调用，换会话后完整回答三项内容并提供有效引用。每模型各一组，属于兼容性和功能冒烟验证，不构成稳定成功率或相对旧协议的统计性能结论。

## 自动回归覆盖

新增测试验证 API 请求确实使用 tools 而不是 JSON 模式、参数分片拼接、拒绝错误/多余调用、工具结果 ID 回传、私有推理不进入展示事件、禁用 Skill 和普通聊天不暴露写工具、后台继续执行独立校验、原生协议错误重试上限和回答流式解码。

完整回归：92 项通过，耗时 24.480 秒。输出保存在 `function-calling-tests.txt`。

## 证据与复现

- `function-calling-glm.json`、`function-calling-deepseek.json`：记忆函数 Schema、参数、后台结果和新会话答案。
- `function-calling-tools-glm.json`、`function-calling-tools-deepseek.json`：创建、修改、澄清以及原生请求/响应轨迹。
- `../scripts/probe_memory_protocol_live.py`：`--recall` 测记忆完整链路，默认调用 GLM，可指定 `--model deepseek`。
- `../scripts/check_function_calling_live.py`：指定 `--model glm` 或 `--model deepseek` 及新的 `--output` 路径，测试三段真实工具流程。

这些脚本会调用已配置接口，应使用新的输出路径保留旧证据。Function Calling 解决传输协议和调用表达问题，不替代事实完整性、冲突治理及遗忘粒度的改进。
