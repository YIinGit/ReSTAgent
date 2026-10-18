"""打印 docx 的字体设置：文档默认、Normal 样式、各段首个 run、以及 w:ins 插入内容的 rPr。"""
import sys
import zipfile
import xml.etree.ElementTree as ET

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def q(t):
    return "{%s}%s" % (W, t)


def fonts_of(rpr):
    if rpr is None:
        return "(无 rPr：继承样式/默认)"
    f = rpr.find(q("rFonts"))
    if f is None:
        return "(rPr 无 rFonts：继承)"
    return "ascii=%s hAnsi=%s eastAsia=%s cs=%s" % (
        f.get(q("ascii")), f.get(q("hAnsi")), f.get(q("eastAsia")), f.get(q("cs"))
    )


def main(path):
    z = zipfile.ZipFile(path)
    styles = ET.fromstring(z.read("word/styles.xml"))
    dd = styles.find(q("docDefaults"))
    if dd is not None:
        rpr = dd.find(q("rPrDefault"))
        rpr = rpr.find(q("rPr")) if rpr is not None else None
        print("文档默认字体:", fonts_of(rpr))
        sz = rpr.find(q("sz")) if rpr is not None else None
        if sz is not None:
            print("  默认字号:", sz.get(q("val")), "(half-points)")
    for st in styles.findall(q("style")):
        sid = st.get(q("styleId"))
        if sid in ("Normal", "a", "CommentText", "annotation text"):
            print("样式 %s:" % sid, fonts_of(st.find(q("rPr"))))

    doc = ET.fromstring(z.read("word/document.xml"))
    body = doc.find(q("body"))
    n = 0
    for p in body.iter(q("p")):
        runs = p.findall(q("r"))
        ins = p.findall(q("ins"))
        text = "".join(t.text or "" for t in p.iter(q("t")))[:24]
        if not text:
            continue
        first = runs[0].find(q("rPr")) if runs else None
        line = "段: %-26s 首个run: %s" % (text, fonts_of(first))
        print(line)
        for i in ins:
            r = i.find(q("r"))
            t = "".join(x.text or "" for x in i.iter(q("t")))[:12]
            print("    [w:ins] %-12s %s" % (t, fonts_of(r.find(q("rPr")) if r is not None else None)))
        n += 1
        if n >= 8:
            break

    if "word/comments.xml" in z.namelist():
        c = ET.fromstring(z.read("word/comments.xml"))
        first = c.find(q("comment"))
        if first is not None:
            r = first.find(".//" + q("r"))
            print("批注首个 run:", fonts_of(r.find(q("rPr")) if r is not None else None))


if __name__ == "__main__":
    for p in sys.argv[1:]:
        print("=" * 20, p)
        main(p)
