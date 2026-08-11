# 提交检查清单

- [x] 自研 Agent Runtime 主循环
- [x] 真实 DashScope/OpenAI 兼容 API 适配
- [x] 四个 Schema 工具与统一 Registry
- [x] 多窗口 Session 隔离和持久化
- [x] 最大轮次、异常包装与结构化 Trace
- [x] Context 基础压缩与 Memory 说明
- [x] Fake LLM 稳定测试和 opt-in 真实 API smoke
- [x] README、AI Prompt 与问题解决记录
- [x] 第三方 MIT 归属说明
- [ ] 轮换已暴露的 Key，配置新的 `DASHSCOPE_API_KEY`，运行真实 API smoke
- [ ] 按 `DEMO_SCRIPT.md` 完成终端/网页录屏
- [ ] 推送到 `https://github.com/AluzzzZ/agent_demo`，确认最终代码链接可访问
- [ ] 提交前确认没有 `.env`、数据库、Trace 或 API Key 被纳入版本控制

建议推送前执行：

```powershell
git init
git add .
git status
git commit -m "feat: implement minimal agent runtime"
git remote add origin <你的空仓库地址>
git push -u origin main
```

若 `git init` 的默认分支不是 `main`，先运行 `git branch -M main`。创建远程仓库和录屏需要使用提交者自己的账号，本地代码不会自动代替这两项材料。
