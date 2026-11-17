# 研迹 PaperTrail

面向计算机／AI 实验型论文的本地研读工具。当前步骤 01 仅实现空文献库、API 健康检查和模型配置状态；PDF 导入、分析及导出尚未实现。

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
