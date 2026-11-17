# 阅读器固定样例

`reader.pdf` 是本项目自行生成的 CC0 测试文件，没有第三方论文内容。生成方法是 `backend/tests/test_pdf_import.py` 的 `make_pdf`，第一页 600×800 点、第二页 420×600 点，各两行固定英文文字。元数据明确为 Fixture Author / Self Authored Title。

用于真实 PDF.js 的页面尺寸、缩放、旋转和证据矩形投影测试，不代表真实论文解析效果。运行 `frontend/` 下的 `npm run test:reader`。
