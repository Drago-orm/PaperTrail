# 当前状态

- 已实现功能步骤00—06；06本地检查通过，真实模型验收仅第一组4项通过，第二组4项受模型使用资格限制。下一功能步骤07等待用户指定，不自行开始。
- 已有：上传/文献库/PDF.js、精读、固定回放、原文语义核查与最多一轮定向返工、人工笔记版本；尚无写作分析、Idea、导出及后续流程集成。
- 06入口：backend/app/agents/verification.py、services/verification_runs.py、services/retrieval.py；prompts/verification-v1.md和revision-v1.md；frontend/src/Notes.tsx。
- POST documents/{id}/verification-runs 接收base_note_id、revise；沿用reading-runs查询。只核查最新保存版本；已支持条目跳过，人工版只核查不自动改写，并发新版本阻止最终写入。
- 数据库v5：notes.verification_run_id关联真实核查任务，旧版null；source_mode仍表示初始精读来源，人工版继承。任务阶段/初查/返工/复查及失败均持久化。
- 范围：单进程/单工作线程；原文40块/16000字符；核查1—9条、正文最多16000字符。初查→最多一次问题项返工→仅复查问题项；每个角色最多2请求，任务最多6请求，单次I/O超时60秒。
- 语义支持由模型判断，引用存在性由程序检查；不确定/未报告继续显示，均只指本次文字范围。03的双栏/旋转/扫描/图表解析限制未改变，不保证自动核查准确性。
- 最近检查：check.ps1 -Scope all 的前端严格类型/生产构建、后端编译/100项通过；核查专项13项通过。受控真实浏览器状态/理由/返工上限/疑点/定位/草稿/人工保护/刷新/旧版拒绝通过。
- 真实API：qwen3.8-27b对自制注入样例第一组4项符合预期，1请求、45660ms、prompt1990/completion1897/total3887 tokens；非真实论文、非通用准确率。第二组及用户改为qwen3.8-flash后仍403 AccessDenied.Unpurchased，用量未知；无依据引文/缺失补造/部分支持/缺失对照尚待补真实验收。
- 本机.env已忽略且未提交密钥；不得自行替换模型。新配置需要服务账户确有模型使用资格，仅格式齐全不代表可调用。依赖/锁文件未变，不重复安装。
- 本轮8000被系统拒绝绑定，浏览器QA用临时18006后端和测试代理；产品默认配置未改。测试服务已停止、临时端口已释放；上游httpx弃用、PDF.js包体积警告保留。
- 原有根plan删除与work材料保留。开发节选docs/development/step-06.md；测试PDF、DB和响应仅在忽略目录。提交/推送以实际Git和远端核验为准。
