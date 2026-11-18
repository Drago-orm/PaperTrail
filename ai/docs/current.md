# 当前状态

- 已完成00—11、12A与12B实现；13／后续等待用户指定，不自行开始。12B为本地BibTeX／RIS元数据交换，未操作Zotero或模型。
- 12B入口：backend/app/services/bibliography.py、api/bibliography.py、tests/test_bibliography.py；frontend/src/Bibliography.tsx与Library。复用Paper、db插入、dedup.signals和现有比较／合并／撤销，无新前端依赖；后端锁定bibtexparser2.1.0／rispy0.10.0，传递pylatexenc。
- DB v8追加Paper.identifiers JSON，旧库默认{}。支持url／isbn／issn／eprint／accession_number／serial_number多值，加入PAPER_EDITABLE与现有合并撤销；文献信息展示导入值。旧v7含真实元数据迁移及撤销测试通过。
- POST /api/bibliography/preview为multipart file/format(bib或ris)，只读准备，返回映射／未映射字段、同批／库内候选与token；POST /import同文件格式+confirmation_token，库或文件变动拒绝旧预览。单批单事务，全新元数据条目，失败回滚；不自动合并、不创建附件／证据／笔记。
- UTF-8／BOM，1—100条／2 MiB。题目／作者顺序／摘要／DOI／年份／标识符保留，缺失不补造；未知字段、原引用键／类型不入库，需保留原文件。BibTeX LaTeX保留字面不执行／解码；未定义字符串／拼接／重复字段明确拒绝。RIS边界、冲突检查，续行合空格，PY／Y1同年，月日仅警告不存。
- POST /api/bibliography/export传format/paper_ids，返回text/filename/count/warnings；所选／列表最多100条，通用misc／GEN及PaperTrail引用键。SN／AN保留值不猜分类，跨格式分类损失有提示；多值BibTeX单字段、RIS换行和无法无损序列化拒绝。标签／任务／方法／数据集／阅读状态／附件／笔记不交换，关键词仅RIS。结构422、解析边界／过期409、缺失404、读写500；已合并被吸收条目不可单独导出。
- 12B检查：专项25项通过；scripts/check.ps1 -Scope all前端严格类型／生产构建、后端编译导入和179 passed／17既有LangSmith警告；PDF.js体积提示保留。修复真实发现的TS收窄、测试目录和解析器容错边界；收尾RIS样例ER行尾空格清理后专项25项再通过，应用代码未变，复用其余检查。
- 12B关键流程：自制CC0中英文／特殊字符／团体作者／缺DOI／重复样例往返；浏览器预览0写入、两格式6条、真实bib／ris下载、标识符合并撤销、损坏文件不写入、无伪附件。0外网／模型POST／UI异常；实际进程重启6条完全一致，截图目视；18013／5183／9224已释放。未验证外部Zotero实际互导，.tools QA忽略。
- 12A仍为证据ID对齐双语阅读（agents/services/api/translation、Bilingual/Reader），本页连续章节候选、6块／4000字、确认发送、20术语和保护／模型配置缓存；Coco独立外链。复杂公式可能原文保留，不保证完整章或语义质量。真实自制短句qwen3.8-flash两次各1请求，已返回977／819 tokens（共1796，费用未知），缓存／重启0请求；未评测长文／中译英。
- 11的2—5篇指定笔记版本比较快照、10可撤销元数据合并、09同源MD／DOCX导出与既有四角色流程保留。来源仍绑定原ID／文件版本，不补缺失证据、不排名或承诺复现／Idea新颖性。
- 06独立第二组未补；08新四角色精读2次超时122136ms、用量未知；独立Idea基于07自制样例支持快照成功（114417ms、2请求、已返回5729 tokens但不全），不是完整流程／真实论文质量通过。12A短句与12B元数据测试不弥补这些限制。
- .env／密钥／PDF／DB／响应及QA不入Git；保留用户根plan删除、work、scripts/test-api.py。本轮真实节选docs/development/step-12b.md；仅提交本步，提交／推送以实际远端核验为准。
