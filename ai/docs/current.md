# 当前状态

- 已完成功能步骤：00—03；下一步：04，等待用户指定。
- 当前已有：健康状态、SQLite 数据与文件存储、PDF 上传/文字证据解析；尚无文献管理 UI、阅读器或真实 AI 调用。
- 入口：backend/app/main.py、services/pdf_import.py、services/storage.py、db.py、schemas.py。
- 上传：POST /api/documents（multipart file，可选 paper_id）；读取：GET /api/documents/{UUID}。真实上限20 MiB/200页，同哈希复用原文档。
- 数据库迁移至v2（parse_reports）；候选、覆盖页、警告和失败原因持久化。数据库 parsed 可带限制，完整覆盖看 report；无文字/旋转/疑似双栏跳过或失败，未执行OCR。
- 最近检查：全量类型/生产构建及72项后端测试通过；新增升级回归后PDF专项14项通过。真实HTTP冒烟和自制样例渲染核验通过。保留原有上游httpx警告。
- 依赖：pdfplumber0.11.10、python-multipart0.0.34；其余沿用既有版本和真实锁文件。
- 当前步骤提交/推送状态以最终Git执行及远端核验为准，不为记录自身哈希另建提交。
- 既有根plan.md删除及work材料保留，不纳入本步；保留并行文档整理提交729a10e。
- 开发证据：docs/development/step-03.md；完整历史：docs/progress.md。
