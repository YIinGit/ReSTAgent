"""活动策划案初审：依据《2026活动部教案》第四讲至第九讲，对高一新成员策划案做初审。

流程：
    解析 docx → 段落编号 → 分块送模型 → 校验 JSON（anchor 逐字定位）→ 合并 → 导出批注

对外入口（service.py）：
    review(docx_path, cfg, submission="协内自用", kind="") -> dict
    export(job_dir, ...) -> 批注版 docx / Markdown 报告
"""
