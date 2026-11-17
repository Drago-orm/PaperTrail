# 当前状态

- 已完成功能步骤：00—05；下一步06，等待用户指定，不自行开始。
- 已有：上传/文献库/PDF.js阅读定位、真实API精读九字段、固定样例回放、运行进度和人工笔记版本；尚无语义核查、写作分析、Idea与导出。
- 数据库v4：notes.source_mode保留live/replay/unknown，人工版继承来源；v3旧笔记迁移为unknown。新运行追加快照，不覆盖人工内容。
- 05入口：backend/app/agents/client.py、reading.py、services/retrieval.py、reading_runs.py、api/reading.py；prompts/reading-v1.md；frontend/src/Notes.tsx。
- 接口：POST/GET documents/{id}/reading-runs、GET reading-runs/{id}、GET/POST documents/{id}/notes；人工保存按base_note_id做冲突检查。
- 范围：单后端进程/单工作线程；40证据块、16000字符、每任务最多2请求、单次I/O超时60秒。引用仅检查存在性，未报告只指本次范围；解析限制仍生效。
- 最近检查：全范围检查的前端严格类型/生产构建通过；补充检索边界与限制常量复用后，最终后端编译/全量87项通过，精读专项11项通过。真实浏览器回放/引文跳页/人工版/重跑与草稿保留/409冲突/刷新通过；测试端口已释放。
- 真实API：qwen3.8-27b，对公开论文1706.03762的Multi-Head关键词范围（物理页3、2块、173字符）成功；1请求、23473ms，prompt1019/completion1184/total2203 tokens。仅局部接口验收，不代表全文分析效果。先前404、超时与结构失败均保留，失败用量不全可知。
- 配置：本机.env已填写且忽略，兼容基址按服务文档配置；密钥未入库。依赖/锁文件沿用04，未新增依赖。
- 已知警告：上游httpx弃用、PDF.js包体积；未禁用或过滤。长生成仍可能超时或结构失败，明确返回失败供重试。
- 原有根plan删除与work材料保留；QA、论文PDF、数据库和响应仅在忽略目录。开发节选docs/development/step-05.md；提交/推送以实际Git和远端核验为准。
