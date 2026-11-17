# 步骤 09 开发节选

日期：2026-11-18（Asia/Shanghai）。以下引文为本轮实际对话节选，命令结果为实际执行记录摘要，未补写历史对话。自制数据／受控保存的条目只验证导出，不是科学结果或真实模型评测。

用户原文：

> 执行步骤 09。应用目录入口是 plan.md，Git 根目录入口是 ai/plan.md；后续文档路径均以应用目录为基准。
> 首次/上下文恢复时只读入口、docs/current.md 和对应步骤卡；已经加载且未变化的规则不重复读取。
> 从步骤卡的代码起点按需定位，只实现本步骤，保持可读和最小改动，不扩展范围。
> 遵守入口的检查矩阵和收尾 G，复用成功检查结果，完成本步 commit、push、远端核验后停止。
> 简要报告结果；不要全文读取全部规格、历史日志、其他步骤或输出完整源码。

助手原文：

> 已确认步骤 09 的范围和现有快照接口。Markdown 与 Word 将共用一份保存的导出快照；下载不会调用模型，也不会随后续编辑改变。接下来补齐来源与版本信息，并验证 Word 的实际分页和下载流程。

实际开发与检查：

- 起点main提交 `437c34f613a29fe14ac240746a5a5bd0b37616ff`，origin为用户指定PaperTrail仓库。保留根plan.md删除、work目录及用户scripts/test-api.py；未读取／暂存这些无关文件，未读取或输出.env密钥。
- 受影响基线 `uv run --locked python -m pytest -q tests/test_ideas.py tests/test_db.py`：23 passed。`uv add "python-docx==1.2.0"` 实际更新pyproject与锁，安装python-docx1.2.0／lxml6.1.3。跨盘硬链接回退复制警告未压制。
- 复用Note／Idea解析、原文证据、ParseReport及数据库事务。保存的两个文件由同一内容序列生成；下载使用冻结文件和哈希校验。未增加模型、数据库迁移、模板引擎或额外角色。
- 导出专项初次2 failed／2 passed：测试发现Markdown对UUID、日期标点过度转义导致文本核对失败。缩小转义范围，保留对Markdown／HTML内容的必要转义；之后1 failed／3 passed为测试未按合法下划线转义核对，修正预期。最终专项4 passed。独立解析器另外验证全部内容，未用导出函数本身作唯一判定。
- 关键测试覆盖指定旧版本／人工新版本、元数据修改和重启后冻结、标签与核查／原文／坐标、所选Idea顺序和实验表、软移除与旧来源缺失、解析limited警告、空／重复／混选／缺失记录、XML控制字符拒绝、损坏文件和写入失败残片清理。所有角色的模型入口在导出测试中被替换成会令测试失败的函数。
- `.\scripts\check.ps1 -Scope all`：前端严格类型与Vite生产构建通过；后端编译及129 passed，17条原有LangSmith弃用警告。PDF.js的500kB体积提示保留。Word目视发现python-docx默认Title含蓝色下划线，显式移除；统一内容层CRLF，补受限解析报告断言后执行 `-Scope backend`，129 passed／17 warnings。前端未再变动，复用成功构建。

助手原文：

> 导出边界测试 4 项已通过，前端类型检查和生产构建也已通过；两端全量检查正在完成。本机缺少 LibreOffice，技能自带渲染器无法运行，我会使用已安装的 Microsoft Word 打开导出文件并转成页面图像，核对中文和长表格布局。

实际集成验收：

- 使用documents技能。技能 `render_docx.py` 实际失败：`FileNotFoundError: LibreOffice soffice.exe was not found on PATH`。本机Microsoft Word可用，隐藏独立COM实例以只读方式打开DOCX，调用 [ExportAsFixedFormat](https://learn.microsoft.com/en-us/office/vba/api/word.document.exportasfixedformat) 导出PDF，再用运行时Poppler转PNG。逐页看完最终4页笔记、9页Idea；中文正常，A4表宽不溢出，长行自然跨页、表头重复，8项长设置的行末标记均保留。关闭自己的Word实例，未修改导出文件。
- 使用仅放在忽略目录的markdown-it-py4.0.0标准解析器，按[官方接口](https://markdown-it-py.readthedocs.io/en/latest/using.html)启用表格，对照DOCX的实际段落与单元格：`note: ... match all 82 blocks/cells`、`idea: ... match all 87 blocks/cells`。HTML／图片／链接样式的原文作为字面文字保存，没有变成活动元素。该QA包没有加入应用依赖或锁。
- 隔离服务18009、Vite5179、Chrome9223；模型配置明确为空，页面如实显示缺失。初次直接运行自制样例脚本在受限环境停滞，核对进程命令行后停止本轮进程，用既有uv锁定环境成功生成。没有使用产品数据或调用模型。
- 浏览器首次下载未落盘：验收脚本使用Windows不接受的正斜杠下载目录。改为原生绝对路径并显式创建目录后成功；产品下载接口当时已返回200，未通过修改产品检查绕过失败。
- 浏览器实际点击笔记、双选Idea的MD／DOCX链接，共4个文件完成下载；逐字节与服务器文件比较一致。打开笔记旧版及Idea旧版，核对捕获ID恰为所选版本；未保存编辑禁用导出，保存新版本不改变此前快照。无模型任务POST，无未捕获UI错误。
- 自制数据、DOCX、PDF／PNG、浏览器截图和本轮QA脚本放在忽略的 `.tools/step09-*`，不入Git。停止前核对PID命令行；结果 `QA listeners remaining: 0`、`Word instances remaining: 0`。

助手原文：

> 浏览器验收已通过：笔记和多选 Idea 的两种文件均实际下载，字节与服务端文件一致；历史版本选择准确，未保存编辑会阻止导出。Word 的 4 页笔记和 9 页长表格已逐页核对，中文正常、表头跨页重复、内容未截断。验收进程和端口已清理，正在记录结果并按 G 提交、推送。

限制：仅验收本机Word的版式，未宣称全部办公套件一致；快照持久保存、无自动过期。导出保留已有核查状态，不会弥补原文解析或模型判断的局限。06第二组、08新完整四角色的真实模型验收限制继续保留，不因本步受控检查而改为通过。提交／上传以实际Git结果为准，不为记录自身哈希追加循环提交。
