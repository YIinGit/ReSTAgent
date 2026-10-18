"""生成回归测试用的示例稿（含典型错误）。仅测试用，依赖 python-docx。"""
from __future__ import annotations

import pathlib
import sys

from docx import Document
from docx.shared import Pt

OUT = pathlib.Path(__file__).resolve().parent / "sample.docx"


def main():
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "宋体"
    style.font.size = Pt(10.5)

    doc.add_heading("激进的自由基", level=1)
    doc.add_paragraph("作者：张瑞杰")

    doc.add_paragraph(
        "其实自由基的定义在生物课本上已经描述得比较清楚了，但是仅凭这么一句话，很多同学还是很难以理解"
        "“自由基”这三个字到底代表着什么。那么，当我们给这个分子足够多的能量的时候，这根化学键就会断裂。"
    )
    doc.add_paragraph(
        "那么，根据共价键断裂的两种方式，以及自由基的定义，哪一种方式能够产生自由基呢？显然是均裂。"
        "通常我们将化学键发生均裂时所需要吸收的能量叫做键能（bond energy），或者倒过来，由气态原子（团）"
        "形成化学键时所放出的能量叫做键能。"
    )
    doc.add_paragraph(
        "那么，有哪些具体的途径可以使一根键均裂形成自由基呢？首先是加热，在极高的温度下面，一切皆有可能。"
        "然后是光照，因为光子也是有能量的。最后是氧化还原。"
    )
    doc.add_paragraph(
        "他说：\"这就是自由基\"。我们举一个例子：我们都知道，H2分子的形成是两个氢原子各拿出自己的一个电子来组成了一条化学键。"
    )
    doc.add_paragraph(
        "这种方法叫做“均裂”。他高兴的说到：“我们终于成功了”！然后我们就开始了下一步工作。"
    )
    doc.add_paragraph(
        "另外，伯利兹城大蓝洞，作者写的是“直径 318m 洞深 125m”，但维基百科查出来是“直径304m 洞深122m”。"
    )

    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "作者"
    table.cell(0, 1).text = "张瑞杰"
    table.cell(1, 0).text = "板块"
    table.cell(1, 1).text = "化学"

    doc.add_paragraph("（全文完）")
    doc.save(OUT)
    print("wrote", OUT)


if __name__ == "__main__":
    sys.exit(main())
