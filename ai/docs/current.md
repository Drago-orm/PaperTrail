# 当前状态

- 已完成功能步骤：00—04；下一步05，等待用户指定，不自行开始。
- 已有：文献上传/编辑/组合筛选、移除恢复、文件版本、PDF.js原文阅读和证据高亮；尚无AI分析和导出。
- 数据库v3：papers.removed；证据/文件不随移除删除。现有PDF解析范围和失败报告继续生效。
- 入口：frontend/src/Library.tsx、Reader.tsx、coordinates.ts、libraryApi.ts；backend/app/api/library.py、services/pdf_import.py。
- 新接口：GET/PATCH papers、PUT papers/{id}/visibility、GET documents/{id}/file。读取原文件必须按文档ID。
- 坐标：后端左上角PDF点，先经原始viewport反转至PDF，再用当前viewport投影。支持缩放、旋转和不同页面尺寸，设备像素比只应用于canvas。
- 最近检查：全量类型/生产构建与后端75项通过；冻结npm ci通过；真实PDF.js的32组坐标回归通过；真实浏览器上传/编辑/刷新/恢复和高亮通过。
- 依赖：PDF.js6.5.438，真实npm锁；其他沿用已有依赖。不重复安装或复查未变化模块。
- 已知警告：上游httpx弃用、PDF.js包体积；未禁用或过滤。
- 数据和QA在忽略目录；原有根plan删除与work材料保留。开发证据docs/development/step-04.md。
- 提交和推送以实际Git执行及远端核验为准，不为记录自身哈希另建提交。
