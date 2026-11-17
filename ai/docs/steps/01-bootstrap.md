# 步骤 01：建立可构建、可启动的最小骨架

**按需参考：**首次搭建才读规格的“最小技术架构”。

**代码起点：**README.md、前后端依赖清单、scripts/。从这些入口沿调用关系查找，避免全仓库通读。

**交付：**前后端、锁文件、环境模板、健康接口、启动和检查脚本、空文献库页面。固定开发端口并在 README 说明。

**验收：**干净安装、前端生产构建、后端健康接口通过；未配置模型时仍能启动，AI 操作显示配置缺失。脚本支持带空格的 Windows 路径，正确清理子进程；后台辅助进程采用隐藏窗口。

**提交信息：**`chore: bootstrap minimal full-stack application`

```text
只执行步骤 01。
创建 React/TypeScript/Vite 前端与 FastAPI 后端，后端提供 /api/health；前端实际请求健康接口。
用 npm 和 uv 生成真实锁文件，加入 .env.example、.gitignore、README、scripts/start.ps1 与 check.ps1；记录实际 Node/Python/npm/uv 版本，并用版本文件和项目配置固定运行时。
只实现空文献库和服务状态，不创建后续功能占位模块；开发代理与跨域仅覆盖需要的本地来源。
验证安装、类型检查、生产构建、后端导入、健康接口及脚本退出码，然后按 G 提交推送。
```
