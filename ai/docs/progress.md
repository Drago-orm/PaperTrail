# 研迹 PaperTrail 开发进度

## 2026-11-17 · 步骤 00：保存计划并准备 Git

- 范围：仅保存[实施计划](../plan.md)、确认 Git 配置和记录本步过程；没有实现应用或创建后续模块。
- 应用项目根目录：`E:\project\chuanzhi\ai`；Git 根目录：`E:\project\chuanzhi`；分支：`main`。
- 用户指定远端：`origin` → `https://github.com/Drago-orm/PaperTrail.git`，已配置并读取远端 `main`。
- 复用根 `.gitignore`，不新增重复规则。`ai/` 下环境文件、数据、依赖、构建产物和 Python 缓存均被忽略；`.env.example` 可跟踪。
- 本步前本地提交为 `98f278e8b65418e10dcf3d29bc321ac5be388bf5`；远端初始提交为 `d60253425e7217178c815c9e8d132d66cd80b14a`，只有一行 README。两者无共同祖先，采用普通合并保留双方历史，不重写提交、不强推。

检查与结果：

- `git status --short`、`git branch -vv`、`git remote -v`、`git log`、`git diff --cached --stat`：已检查。未发现适用的 `AGENTS.md`；本步开始时暂存区为空。
- `git check-ignore -v --no-index`：已验证 `ai/.env`、`ai/.env.local`、`ai/data/`、前后端依赖及缓存路径受忽略；另验 `.env.example` 未被忽略。
- `git ls-remote --symref https://github.com/Drago-orm/PaperTrail.git HEAD refs/heads/main`、`git fetch origin main`、`git ls-tree`、`git show`：成功读取远端。首次沙箱内访问因 `getaddrinfo() thread failed to start` 失败，获准在沙箱外重试后成功。
- `git merge-base HEAD origin/main`：返回 1，确认两个现有历史无共同祖先；不是应用测试失败。
- `git merge --no-commit --no-ff --allow-unrelated-histories origin/main`：无冲突，停在阶段提交前；暂存的根 `README.md` 原样来自远端初始提交。
- Python 标准库内联检查（PowerShell here-string 传入 `python -`）：3 份文档严格 UTF-8 解码、Markdown 围栏、3 个本地链接、步骤 00—13 连续编号与 12A/12B 子步骤、项目路径和计划内检查命令均通过；没有嵌套仓库。计划中的脚本属于步骤 01，未声称已经存在。
- `git diff --cached --check`：通过。另用 Python 核对暂存路径仅有本步 3 份文档及远端原始 README，暂存内容可严格 UTF-8 解码；根计划删除保持未暂存，未夹带 `work/` 材料。已检查差异，无新增依赖、重复规则或废弃模块需要清理。
- 前端安装、类型检查、生产构建、后端导入、测试和启动：未执行；步骤 00 尚无应用代码，不宣称这些检查已通过。

真实开发证据：[本聊天节选及执行记录](development/step-00.md)。提交哈希及推送结果由执行汇报和远端核验提供，不为记录自身哈希另建提交。

限制：根目录 `plan.md` 的删除及 `work/` 材料是本步前已有改动，保持未暂存。因此提交树仍保留旧的根计划副本；后续开发以 `ai/plan.md` 为准。本步不新增依赖、脚本或应用功能。

## 2026-11-17 · 步骤 01：最小可构建、可启动骨架

完成 React/TypeScript/Vite 中文空文献库、真实健康请求及加载/失败/重试状态；FastAPI 健康接口和模型配置缺失/错误提示；环境模板、版本约束、真实锁文件、启动与检查脚本和 README。复用父仓库忽略规则，新增的项目忽略项仅为本地 `.tools/`。未添加后续业务模块。

实际环境：Node 24.11.1、npm 11.6.2、Python 3.14.3、uv 0.13.8。前端固定 React 19.3.1、Vite 7.3.8、TypeScript 7.0.2；后端依赖版本见 `pyproject.toml` 和 `uv.lock`。

已执行的检查：

- `npm ci --offline`：全新前端依赖目录冻结安装通过，复用已下载的官方包缓存；未将其记为首次网络下载。
- `npm run build`、`npm ls --depth=0`：严格类型检查、生产构建和依赖树通过。在线 `npm audit fix` 在兼容范围修复 4 项间接依赖问题，最终 0 vulnerabilities；没有强制升级或 overrides。
- `check.ps1 -Scope backend -Install`：在全新 `.tools/clean-backend` 环境执行 `uv sync --locked`、编译、pytest，通过 10 项测试。
- `check.ps1 -Scope all`：前端构建、后端编译/导入和 10 项测试全部通过。保留一条上游 Starlette/TestClient 的 httpx 弃用警告，未过滤；跨盘 uv 缓存使用复制回退是性能警告。
- PowerShell AST 检查通过；含空格临时项目中注入 npm 退出码 37，确认失败立即传播、未打印成功。
- 真实含空格目录中复制必要源码和锁，使用 `npm ci --offline`、`uv sync --locked --offline` 全新安装并启动通过；这是缓存安装验证，不是冷网络计时。
- 启动脚本实际验证：拒绝已有端口且保留原占用者；前端退出清理后端；后端父进程退出清理全部服务。复现 uv 包装进程遗留问题后改为冻结同步再直接启动 Python，未引入进程管理框架。
- `start.ps1` 启动，真实 HTTP 健康及 Vite API 代理通过；Chrome 实际渲染与截图目视检查通过。Ctrl+C 后 8000/5173 均可重新绑定。仅 junction 的 Vite 夹具错误通过改用真实目录验证解决，未修改生产优化配置。
- `git diff --cached --check` 通过；暂存清单核对仅 23 个本步明确文件，根计划删除、`work/`、环境密钥、数据、依赖和构建目录均未纳入。

已审查本步文件和依赖，无废弃插件、样例论文、密钥、构建产物或依赖目录需提交。真实过程及失败修复见 [step-01.md](development/step-01.md)。

限制：本步不提供导入、持久化、PDF 解析或 AI 调用；配置齐全只表示字段和 URL 格式有效，未执行真实模型 API 冒烟。既有根计划删除与 `work/` 材料继续保留。步骤 02 仅在本步提交推送并核验成功后开始（用户本轮明确要求按顺序执行两步）。
