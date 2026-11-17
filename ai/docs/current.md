# 当前状态

- 已实现功能00—08；下一步09等待用户指定，不自行开始。08四角色受控端到端通过；真实Idea独立调用成功，新完整四角色试跑在精读超时。06独立样例第二组未补真实验收。
- 已有文献库/PDF.js、精读/写作/核查/Idea四角色、可恢复缓存流程、人工笔记与Idea版本、单因素验证计划；尚无导出、合并。
- 08入口：backend/app/agents/ideas.py、services/ideas.py与idea_runs.py、api/ideas.py、schemas.py；prompts/ideas-v1.md；frontend/src/Ideas.tsx、ideasApi.ts。
- Idea来源仅作者陈述/系统判断的supported条目，须匹配成功核查快照和解析版本。来源最多6条/3文件，原文40块/16000字符，条目正文12000字符；源文件/笔记/条目/证据ID不可改写，软移除后保留。
- v6在原Idea表增加root/parent/version、人工标记、生成任务及结构化实验，来源增加item_id；旧文本/缺失来源明确显示，不补造历史。编辑和状态变化追加新版本，旧基版409；生成不覆盖人工版。
- 实验两组共用模型、数据集、划分及设置键，只有changed_factor一项不同；指标/评估协议/待确认条件分开。计划未执行、未外部查新，不保证新颖性/效果/发表。
- GET idea-sources；POST idea-runs；GET ideas?q=&status=、ideas/{id}、ideas/{id}/versions；POST ideas传draft/status/base_idea_id。任务沿用reading-runs查询。
- 新workflow默认include_idea=true、四节点、正常最多16请求，含一次恢复硬预算26；独立Idea最多2请求。旧三角色记录按原范围运行，include_idea=false可保持三角色。
- Idea缓存涵盖配置/Prompt、文件/解析/原文、支持快照、当前笔记版本、相关人工Idea修订和兴趣/资源条件；中断恢复保留已完成结果，来源不足明确失败。
- 最近检查：初始相关41项，新增Idea专项14项；check.ps1 -Scope all前端类型/生产构建、后端导入编译/125项通过，后续仅前端缺失提示复核通过。浏览器新增/编辑/筛选/状态、双文件原文URL/页码/高亮、软移除来源、重跑保护/历史/重启及受控四角色通过。
- 真实：四角色任务2bd614d9-d11b-4f91-a182-864242ed6206精读2次超时，122136ms，用量未知。独立Idea任务8030c0d9-1a84-4f41-97c6-1e947bca9d19使用上步真实支持快照成功：114417ms、2请求、已返回5729 tokens，超时/重试用量不全。草稿单因素pdf_layout，6项待确认，来源2条目/4证据关联/笔记v3；仅自制样例，不代表真实论文质量。
- 依赖/锁未变；LangSmith弃用和PDF.js体积警告保留。QA用18008，测试进程已停、端口释放；未改产品默认端口。03解析限制及自动核查不保证准确性的限制继续有效。
- .env、密钥、PDF、DB、原始响应不入Git；原有根plan删除、work、用户scripts/test-api.py保留。开发节选docs/development/step-08.md；提交/推送以实际Git远端核验为准。
