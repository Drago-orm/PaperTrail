# 研迹 PaperTrail

面向计算机／AI 实验型论文的本地研读工具。当前实现步骤 01—04：上传、元数据编辑、组合文本筛选、可恢复移除，以及 PDF.js 原文阅读和证据定位。AI 分析和导出尚未实现。

项目根目录是本文件所在的 `ai/`；Git 仓库位于父目录。所有命令在项目根目录用 PowerShell 7 执行。

## 环境与安装

本步实际使用 Node.js **24.11.1**、npm **11.6.2**、Python **3.14.3**、uv **0.13.8**。前端版本文件和 `package.json`、后端 `.python-version` 和 `pyproject.toml` 固定运行环境及依赖；提交两个包管理器实际生成的锁文件。

先安装以上 Node/Python 版本。若 PATH 中没有 uv，可在项目内安装工具：

```powershell
python -m pip install --target .tools uv==0.13.8
```

脚本优先使用 PATH 中的 uv，否则使用 `.tools/bin/uv.exe`；该工具目录不提交。

首次安装或依赖有变更时执行冻结安装及检查：

```powershell
.\scripts\check.ps1 -Scope all -Install
```

日常检查不改锁文件：

```powershell
.\scripts\check.ps1 -Scope frontend
.\scripts\check.ps1 -Scope backend
.\scripts\check.ps1 -Scope all
```

前端执行 `npm run build`，包含 TypeScript 检查和 Vite 生产构建。后端执行 `uv run --locked python -m compileall -q app` 和 `uv run --locked python -m pytest -q`，测试包含应用导入与健康接口。任一步失败均返回非零退出码。

## 启动与配置

```powershell
.\scripts\start.ps1
```

固定地址：前端 [http://127.0.0.1:5173/](http://127.0.0.1:5173/)，后端健康接口 [http://127.0.0.1:8000/api/health](http://127.0.0.1:8000/api/health)。Vite 将同源 `/api` 请求代理到后端；服务仅绑定本机，不开放额外跨域来源。

脚本拒绝占用端口，先用 `uv sync --locked` 核对后端环境，再直接启动虚拟环境 Python；健康接口和页面真实就绪后报告启动成功。按 Ctrl+C 停止；一个服务退出时也会清理本次启动的另一服务及其子进程。运行日志在被忽略的 `data/logs/`，失败时检查对应日志。

无需模型配置也可启动。可将 `.env.example` 复制为 `.env`，填写 `PAPERTRAIL_MODEL_BASE_URL`、`PAPERTRAIL_MODEL_NAME` 和 `PAPERTRAIL_MODEL_API_KEY` 后重启。已有环境变量优先。留空和非法 URL 会明确显示；健康接口不返回密钥。此阶段检查配置齐全与格式，尚未验证真实模型连接，也没有实时分析或样例回放。

后续范围见 [plan.md](plan.md)，实际检查和开发证据见 [docs/progress.md](docs/progress.md)。

## 数据与文件

启动时初始化 `data/papertrail.sqlite3`，用 `PRAGMA user_version` 依序执行事务迁移；版本不支持或初始化失败会阻止启动。每个连接启用外键，写入使用参数绑定和事务。数据库及文件都被 Git 忽略；测试注入临时数据目录，不修改已有文献数据。

模型覆盖文献、文档版本、证据块、笔记快照、Idea 及来源、运行与阶段、合并事件。笔记用 Pydantic 校验的 JSON 保存条目与核查状态，人工修订插入新快照并保留父版本；证据和笔记来源绑定具体文件版本。这里仅做结构、证据存在性与文件归属检查，语义核查将在后续步骤实现。坐标约定为物理页从 1 开始、左上角原点、PDF 点坐标，边界受页面宽高约束；阅读器按下述 viewport 约定投影。

确定性服务接口位于 `backend/app/db.py`（`create_paper`、`create_note`）和 `backend/app/services/storage.py`（`store_document`、`document_path`）。文件由服务器 UUID 保存为 `data/documents/<UUID>.pdf`，原文件名仅为元数据；按文档 ID 查数据库后读取，并检查实际路径未越出数据目录。记录真实 SHA-256 和递增版本，失败时回滚并清理本次写入。

底层存储只保存字节并初始化为 `pending`；通过以下上传接口才会执行 PDF 检查和解析。

## PDF 上传与解析

`POST /api/documents` 接收 multipart 的 `file`，可选 `paper_id`。在后端 [API 文档](http://127.0.0.1:8000/docs) 操作；真实文件限制 20 MiB、200 页。按文件签名及 pdfplumber 打开结果检查实际格式，不信任 MIME 和文件名。错误格式返回 415、大小超限返回 413；解析失败仍保存文档 ID，`document.parse_status` 和 `report.status` 为 `failed`，附错误原因。

`GET /api/documents/{document_id}` 返回持久化报告和证据。报告包含解析器版本、物理页数、实际提取页列表、警告和标题/作者候选（缺失为 null），不自动覆盖元数据。同 SHA-256 重传复用原文献/文档与证据 ID；原先仅存储的文件可在重传时解析。

按视觉文字行生成段落候选，保存真实原文、章节候选、物理页和坐标。检测到双栏/复杂布局、旋转页时跳过并警告；空白、扫描、损坏及加密文件明确失败或受限，不执行 OCR。含图表页仅读取文字，报告 limited。即使 parsed 也仅表示文字提取完成，不保证通用版式阅读顺序或图表准确性；完整覆盖以 report 为准，数据库 parsed 可能带限制。论文中的指令文字只保存为原文。

固定自制、CC0 样例生成器在 `backend/tests/test_pdf_import.py`，涵盖单栏、双栏、图像页、空文本、旋转和坐标。未使用第三方论文全文。

## 文献库与阅读器

网页上传后直接显示入库文献。题目、作者、摘要、关键词、标签、阅读状态可编辑保存；数组字段每行一项，标题/作者候选需用户采用并保存。关键词筛选是文本匹配，多个条件按 AND 组合，不是语义检索。

单条移除将该文献放入移除区，可选择恢复；原文件、证据和来源仍保留。文件版本可切换，点击原文证据会跳转物理页并高亮。阅读器支持缩放、旋转、手动页码和原文件链接；坐标先按原页面 viewport 转为 PDF 坐标，再投影至当前 viewport，不把设备像素倍率重复乘进 CSS。

接口：GET /api/papers（q/title/author/abstract/keyword/tag/reading_status/removed）；PATCH /api/papers/{UUID} 编辑元数据；PUT /api/papers/{UUID}/visibility?removed=true 或 false；GET /api/documents/{UUID}/file 按文档 ID 读取 PDF。数据库顺序迁移至 v3，新增 removed 标记。解析限制仍以步骤03报告为准。

运行 frontend/ 中的 npm run test:reader，使用 fixtures/reader.pdf 的 CC0 双尺寸样例，验证真实 PDF.js 的缩放与旋转坐标。PDF.js 6.5.438 使用本地 worker，依赖及锁文件由 npm 实际生成。构建保留 PDF.js 带来的体积告警，不调高阈值隐藏。
