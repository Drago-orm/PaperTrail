# 交付与演示清单

## 可用材料

| 材料 | 用途 |
|---|---|
| [复现指南](reproduce.md)、[评测记录](evaluation.md) | 预装工具前提、真实调用限制、首次安装与关键路径计时 |
| [CC0 样例](../fixtures/README.md) | 固定回放、物理页跳转、重传去重、导出 |
| [步骤 05](development/step-05.md)、[步骤 06](development/step-06.md)、[步骤 08](development/step-08.md) | 精读、核查、Idea 的真实开发对话节选，对应提交 `c2e5efc`、`161dd6d`、`437c34f` |
| [步骤 13](development/step-13.md) | 释出验收中的真实对话节选、端口和模型超时处理 |
| [版本进度](progress.md) | 按步的开发与验证记录，可结合 Git 查看提交差异 |

录制时使用独立数据目录中的公开论文与 CC0 样例。提前核对材料中的作者、许可、模型模式与用量。公开截图和视频前检查凭据、私有数据及个人信息。对话文件是节选，完整开发会话应从原会话另行留存并核对。

依赖版本和下载来源由 [npm 锁文件](../frontend/package-lock.json) 与 [uv 锁文件](../backend/uv.lock) 记录。已安装直接依赖中，React、Vite、FastAPI、LangGraph、pdfplumber、Pydantic、python-docx、bibtexparser 和 rispy 的包元数据列为 MIT。PDF.js、TypeScript、python-multipart 为 Apache-2.0，uvicorn 和 python-dotenv 为 BSD-3-Clause。第三方论文作者与 CC BY 许可见[评测记录](evaluation.md)。

## 约 6 分 30 秒的录制安排

| 时间 | 展示内容 |
|---|---|
| 0:00–0:40 | 工具版本、冻结安装计时、本地启动与配置状态 |
| 0:40–1:35 | 两篇机器学习软件论文、真实解析范围、重复文件复用、候选关系 |
| 1:35–2:40 | 精读／写作／核查与证据跳转、人工新增版本、失败与恢复记录 |
| 2:40–3:45 | Idea 来源和单因素对照、保存重开、同一快照的 MD／DOCX、合并撤销 |
| 3:45–6:05 | 展示至少两个完整的需求与修正来回，并演示精读、核查、Idea 三个核心迭代的真实会话、修改及检查结果 |
| 6:05–6:30 | 固定回放标记、评测局限及复现指南入口 |

录制有真实服务时优先展示本次实际保存的版本。备用固定回放须保留“未调用模型”标记。断网时可展示测试输出，明确受控响应的边界。可按下列命令核对关键行为。

```powershell
Push-Location backend
uv run --locked python -m pytest -q tests/test_reading.py tests/test_verification.py tests/test_workflow.py tests/test_ideas.py tests/test_exports.py tests/test_dedup.py
Pop-Location
```

计划中的成片为 5–8 分钟、1080p MP4，至少两分钟展示实际 AI 协作开发。技术 PDF 计划不超过 30 页，覆盖需求控制、架构、原创贡献、验证和复现。交付文件目前是清单与仓库材料，视频、技术 PDF 和报名尚待制作。提交比赛材料前，按实际赛事官方最新条款复核。
