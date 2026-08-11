# 录屏操作脚本

建议录制 4～6 分钟，画面同时显示两个 PowerShell 窗口和 `data/traces.jsonl`。

## 1. 环境与测试

```powershell
.\.venv\Scripts\Activate.ps1
$env:DASHSCOPE_API_KEY="轮换后的 API Key"
$env:DASHSCOPE_BASE_URL="https://dashscope.aliyuncs.com/api/v1"
$env:DASHSCOPE_MODEL="deepseek-v4-pro"
python -m pytest -m "not live_api"
```

展示稳定测试全部通过。不要在画面中打印 API Key。

## 2. 窗口 1：天气、待办和工具追问

```powershell
minimal-agent --user user-a --session window-1
```

依次输入：

```text
查一下上海今天的天气，如果可能下雨就帮我记一个“下班带伞”的待办。
那明天呢？
列出这个窗口的待办。
```

展示回复后的 `trace_id` 与两轮以上工具循环。

## 3. 窗口 2：独立会话

```powershell
minimal-agent --user user-a --session window-2
```

依次输入：

```text
帮我记一个“周五提交周报”的待办。
列出待办。
你知道窗口 1 的天气查询吗？
```

窗口 2 应只有周报待办，也不应获得窗口 1 的对话内容。

## 4. Session 恢复

退出窗口 1 后重新运行：

```powershell
minimal-agent --user user-a --session window-1
```

输入：

```text
继续刚才的话题，我明天需要带伞吗？我的待办是什么？
```

展示相同 Session 能恢复历史与 Todo。

## 5. Calculator、Search 与 Trace

输入：

```text
计算 (17 * 19 + 7) / 5。
搜索 session 隔离，第二条结果展开说说。
```

最后另开窗口查看 Trace：

```powershell
Get-Content data\traces.jsonl -Tail 20
```

指出 `trace_id`、iteration、工具名、参数、耗时、状态和退出原因。
