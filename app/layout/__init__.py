"""三审（版面/排版）检查子系统。

模块：
- model.py   数据模型（页面、网格、框、文字段、问题项）
- idml.py    IDML 解析（结构层）
- pdf.py     PDF 解析（读者所见层，依赖 vendor/pypdfium2）
- rules.py   规则引擎（C 为主 + B 兜底）
- report.py  报告输出与页面高亮数据
"""
