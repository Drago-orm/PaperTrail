# 本地复现指南

在 Windows 预装 Git、PowerShell 7、Node.js 24.11.1（npm 11.6.2）、Python 3.14.3、uv 0.13.8 和浏览器。以下时间从工具预装后开始计算。首次安装需要访问 npm Registry 和 PyPI。真实模式还需要可用的 OpenAI 兼容模型服务和本机凭据。

在新的可写目录克隆后进入应用目录。若要测首次耗时，请指定两个位于当前目录下的新空缓存路径。开发机日常复用原缓存即可。

```powershell
git clone https://github.com/Drago-orm/PaperTrail.git 'PaperTrail 验收'
Set-Location 'PaperTrail 验收/ai'
$env:npm_config_cache = Join-Path $PWD '.tools/reproduce-npm-cache'
$env:UV_CACHE_DIR = Join-Path $PWD '.tools/reproduce-uv-cache'
$env:UV_PYTHON_DOWNLOADS = 'never'
Push-Location frontend
npm.cmd ci
if ($LASTEXITCODE) { throw 'npm ci 失败' }
Pop-Location
Push-Location backend
uv sync --locked
if ($LASTEXITCODE) { throw 'uv sync --locked 失败' }
Pop-Location
```

可运行 `.\scripts\check.ps1 -Scope all` 做完整构建与测试。启动用 `.\scripts\start.ps1`，端口冲突时可指定 `-BackendPort 18014 -FrontendPort 5184`。打开脚本打印的前端地址。服务状态和 `/api/health` 检查本地连接与配置格式。日志在 `data/logs/`，按 Ctrl+C 停止。

## 真实接口

1. 将 `.env.example` 复制为 `.env`，填写服务基址、模型名、密钥，重启。使用支持 `enable_thinking` 的混合思考模型时，可在本机选填 `PAPERTRAIL_MODEL_ENABLE_THINKING=false`。此项会改变模型输出和耗时，也会区分缓存。留空采用服务默认值。参数见[服务方接口文档](https://help.aliyun.com/en/model-studio/qwen-api-via-openai-chat-completions)。
2. 从评测记录的原始来源下载已授权 PDF。上传 AutoKeras，核对解析覆盖后打开文献。运行“精读→写作→核查→Idea”。本次只会发送所选文字范围及任务条件给配置的服务。生成 Idea 至少需要一条带原文证据的支持结论。
3. 打开最终笔记，点“查看原文证据”，核对文件版本、物理页与高亮。需要补充时保存新的人工修订。打开本次 Idea，核对来源以及最小对照实验的两组设置，编辑后保存新版本，并从 Idea 库重开。
4. 在笔记或 Idea 版本下准备导出，下载 MD 和 DOCX，核对两者的同一快照 SHA-256、来源 ID、内容和待核验项。相同 PDF 再上传会按字节哈希复用。不同文件的相似标题或 DOI 只产生候选，人工比较后再决定关联或合并。

模型超时、配置错误或缺少支持来源时，查看任务阶段和错误。完整流程支持一次恢复，已保存的节点可以复用。如果已保存核查但自动 Idea 失败，可在 Idea 库选择被支持的来源，填写人工 Idea 与对照实验后保存。远端延迟和账单受服务影响，详见[评测记录](evaluation.md)。

## 固定回放

模型配置可留空。上传仓库的 CC0 样例 `fixtures/reader.pdf`，选择“固定样例回放”，运行精读。结果应标记未调用模型。点击方法条目的出处，定位到物理第 2 页；可保存人工笔记，再准备 MD／DOCX 导出。再次上传同文件会复用文件版本。回放覆盖这条确定性路径，写作、语义核查和生成 Idea 在界面中仍需真实服务。

跨四角色、Idea 保存重开、核查失败恢复和版本保留可以按[演示清单](demo.md)运行受控测试。固定响应在测试内注入，分别记录程序正确性与真实模型表现。
