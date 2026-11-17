# 当前状态

- 已实现功能步骤00—07；下一步08等待用户指定，不自行开始。07真实三角色流程恢复成功；06独立注入错误第二组尚未补真实验收。
- 已有：上传/文献库/PDF.js、精读、写作分析、原文核查及一次返工、人工笔记、可恢复LangGraph三节点流程；尚无Idea与导出。
- 07入口：backend/app/agents/writing.py、services/workflow.py、services/reading_runs.py、verification_runs.py、api/reading.py；prompts/writing-v1.md；frontend/src/Notes.tsx。
- POST documents/{id}/workflow-runs 输入section/query/interest/resources/revise；POST workflow-runs/{id}/retry沿用原输入恢复一次。模型配置变更或并发人工新版本需启动新流程；成功节点复用，不覆盖人工内容。
- SQLite仍v5：笔记含writing_items，缓存与检查点保存于现有run_steps。缓存键含角色、规范化范围/条件、文件哈希/解析版本、原文/证据、Prompt版本/内容、模型配置/凭据摘要、笔记与核查快照。密钥不保存到记录。
- LangGraph1.2.22及实际传递依赖由uv新增并锁定，前端依赖未改。图按精读→写作→核查执行，单进程/单工作线程，成功节点立即保存快照；跨进程恢复已验证。
- 原文仍40块/16000字符；写作5项，精读9项；核查总正文最多16000字符。流程核查每批最多7项，最多一轮定向返工；正常最多14次请求，含一次恢复硬预算24次。旧任务沿用自身更低预算。中断期间未知请求不填0，预留预算保留。
- 检查：初始相关33项通过；最终工作流专项11项通过，check.ps1 -Scope all前端严格类型/生产构建、后端导入编译/111项通过。浏览器受控故障、真实后端重启、缓存恢复、写作引用跳页、人工编辑/重跑/刷新通过，测试端口已释放。
- 真实API：qwen3.8-flash对CC0自制reader.pdf的4块/60字符运行。首轮精读/写作成功、14项合并核查超时；分批后仅恢复核查成功，精读/写作未重复调用。任务913a6ff4-6d0d-4c88-9065-12c30384b94a累计393534ms、已知8请求、已返回17318 tokens，超时用量不全/费用未知。最终支持2、部分支持2、范围未报告10，非真实论文质量认证。
- 已知：较大输入仍可能超时，自动核查不保证准确性；03解析限制不变。06独立注入错误第二组曾被403拒绝，本轮未重测该样例，不将07验收替代它。
- 已知警告：LangSmith对Python asyncio API弃用、PDF.js包体积；保留未过滤。新依赖含httpx2，原TestClient/httpx警告不再出现。QA用临时18007，未改产品默认端口或复测上轮8000系统限制。
- 本机.env忽略且密钥未提交；原有根plan删除、work材料及用户新增scripts/test-api.py保留。开发节选docs/development/step-07.md；测试DB、原始响应与截图仅在忽略目录。提交/推送以实际Git核验为准。
