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
