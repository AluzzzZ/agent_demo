# AI Prompt 与问题解决记录

## 原始任务 Prompt 摘要

从零实现一个不依赖现有 Agent 框架的最小 Agent Runtime，包含模型自主工具选择、至少三个 Schema 工具、输出解析、多窗口 Session、长对话记忆与基础压缩、最大轮次、异常与 Trace，并构建测试；必须使用真实 LLM API，提交 README、录屏和 AI 辅助记录。允许参考 `shareAI-lab/learn-claude-code`，但不能原样提交。

## 设计 Prompt

用于指导本次实现的约束式 Prompt：

```text
以 Python 和阿里云百炼 OpenAI 兼容 API 实现一个可解释的最小 Agent。
禁止 Agent 框架；SDK 只能负责 HTTP/API。
主循环必须与工具实现解耦；工具由 name/description/input_schema/handler 注册。
Session 使用 (user_id, session_id) 隔离并可跨进程恢复。
模型历史必须保留合法的 tool_use/tool_result 配对，并由 Provider Adapter 转换协议。
只记录公开决策摘要，不提取或持久化隐藏思维链。
所有关键行为必须能用 Fake LLM 稳定测试，真实 API 测试显式 opt-in。
```

Context 压缩使用的真实模型 Prompt 位于 `DashScopeLLM.summarize`，要求保留用户目标、关键事实、工具结论、约束和未完成事项，禁止补充事实或记录隐藏推理。

## 参考仓库评估

可复用的设计：

- `s01_agent_loop` 的单循环和 stop/tool 分支；
- `s02_tool_use` 的 Schema 工具与 handler 分发；
- `s05_todo_write` 的 Todo 工具形态；
- `s08_context_compact` 的分层压缩思路；
- `s11_error_recovery` 的重试/恢复方向。

不能原样使用的部分：

- 教程示例大量使用进程内 `history` 或全局 Todo，无法隔离两个窗口；
- `TOOLS` 和 `TOOL_HANDLERS` 双表没有形成带校验的统一 Registry；
- 核心示例是无界 `while True`；
- 终端打印不等同于可关联请求的结构化 Trace；
- 测试没有围绕本题逐项验收。

因此没有裁剪综合示例，而是保留通用 Agent Loop 思想，重新实现本题所需边界。这样既符合 MIT 许可，也更容易在答辩中解释每层职责。

## 主要问题与取舍

### 1. “提取思考过程”与隐藏 chain-of-thought

不应要求或保存模型隐藏思维链。本项目把工具调用前模型主动输出的公开文本称为 `reasoning_summary/decision_summary`；隐藏 `thinking` block 会被忽略。Trace 足以解释“哪一轮为何调用哪个工具”，不会泄露内部 CoT。

### 2. 工具消息不能被随意裁剪

内部历史里的 assistant `tool_use` 与后续 user `tool_result` 是协议配对，DashScope Adapter 会转换为 OpenAI `assistant.tool_calls` 与 `tool` 消息。简单保留最后 N 条可能从工具结果中间开始，导致 API 请求无效。`ContextManager._safe_cut` 把切分点回退到完整用户轮次，并由测试验证压缩后仍能继续对话。

### 3. Session 为什么用 SQLite

全局变量只能服务一个进程；单 JSON 文件在两个终端同时写入时容易覆盖。SQLite 支持复合主键、事务、WAL 和跨进程恢复，且无需部署额外服务，适合最小项目。

### 4. Summary 放在哪里

最近完整消息继续使用原生 `messages`，保持工具协议；长期摘要和 Todo 放在 System Prompt 的明确 XML 标签中，每次模型调用前召回。旧原文仍留在数据库中，只从发送 Context 中移除，兼顾审计与成本。

### 5. 为什么 Search 与 Weather 使用 mock

题目允许 mock Search。固定数据使录屏和单测可重复，也不会引入额外 Key、网络抖动或第三方条款。真实 LLM API 仍用于自主选择工具、消费结果并形成回复。

### 6. 测试为什么默认使用 Fake LLM

单测若依赖真实模型会昂贵、缓慢且不确定。脚本化 Fake LLM 精确覆盖每条 Runtime 分支；另有显式开启的真实 DashScope smoke test，证明协议适配可用。

### 7. 为什么自动转换 BASE_URL

用户给出的 `https://dashscope.aliyuncs.com/api/v1` 是 DashScope 原生 SDK 地址；OpenAI Chat Completions 的 Function Calling 地址是 `https://dashscope.aliyuncs.com/compatible-mode/v1`。Adapter 接受两种配置并把前者的后缀转换为后者，避免把凭据或服务地址硬编码在 Runtime。

## 调试与验证记录

- 解析器：验证 text、多个 `tool_use`、非法 block 和隐藏 thinking 过滤。
- 工具：验证 Registry 重名、Schema 参数错误、安全计算和未知工具。
- Runtime：验证直接回答、工具回填、多工具、普通追问、工具追问和最大轮次。
- Session：验证 `user-a/window-1` 与 `user-a/window-2` 隔离，以及重新打开数据库后的恢复。
- Context：用很小阈值强制压缩，验证 summary 召回。
- Trace：验证一次请求的事件顺序与统一 `trace_id`。
- Provider：验证 DashScope URL、工具 Schema、历史消息和模型响应的双向转换。
- Live API：默认跳过；设置 `RUN_LIVE_API_TEST=1` 后调用真实 `deepseek-v4-pro` API。

## 已知边界

- Context 阈值使用字符数近似 Token；生产环境应换成模型 tokenizer/token counting API。
- JSONL 适合演示与单机审计；高并发生产环境可改为数据库或 OpenTelemetry。
- mock Search/Weather 不代表实时数据。
- 内置工具都为快速本地函数，尚未实现通用的强制进程级工具超时/沙箱。
- 当前只提供 CLI；网页 UI 可作为独立展示层接入同一 Runtime。
