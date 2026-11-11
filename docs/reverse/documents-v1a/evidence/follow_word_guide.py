"""Follow the documents-v1a Word reverse guide in LibreOffice Writer via UNO.

Each numbered guide step is executed as one UNO operation with the guide's exact
value, dispatched by the step's Word menu path. Unknown menu paths fail loudly,
so a guide step that has no executable meaning is a finding, not a silent skip.

Usage (inside the LibreOffice container, soffice listening on port 2002):
    python3 follow_word_guide.py guide-steps.json OUTDIR
"""

import json
import sys
import time

import uno
from com.sun.star.beans import PropertyValue
from com.sun.star.table import BorderLine2
from com.sun.star.text.ControlCharacter import PARAGRAPH_BREAK


def prop(name, value):
    item = PropertyValue()
    item.Name, item.Value = name, value
    return item


def connect():
    local = uno.getComponentContext()
    resolver = local.ServiceManager.createInstanceWithContext("com.sun.star.bridge.UnoUrlResolver", local)
    for _ in range(60):
        try:
            return resolver.resolve("uno:socket,host=localhost,port=2002;urp;StarOffice.ComponentContext")
        except Exception:  # noqa: BLE001 - UNO raises NoConnectException until soffice listens
            time.sleep(1)
    raise SystemExit("soffice did not start")


def inches(text):
    return round(float(text.strip('"')) * 2540)


class Writer:
    def __init__(self, doc):
        self.doc = doc
        self.text = doc.Text
        self.cursor = self.text.createTextCursor()
        self.style = doc.StyleFamilies.getByName("PageStyles").getByName("Standard")
        self.first_paragraph = True
        self.table = None
        self.log = []

    def paragraph(self, style, value):
        if not self.first_paragraph:
            self.text.insertControlCharacter(self.cursor, PARAGRAPH_BREAK, False)
        self.first_paragraph = False
        self.cursor.ParaStyleName = style
        self.text.insertString(self.cursor, value, False)

    def run(self, step):
        tool, value, action = step["tool"], step["value"], step["action"]
        if tool == "Home ▸ Styles ▸ Normal ▸ Modify… ▸ Format ▸ Font…":
            # Word "Normal" == Writer "Default Paragraph Style" (Standard); Latin + Complex (Thai) font.
            default = self.doc.StyleFamilies.getByName("ParagraphStyles").getByName("Standard")
            default.CharFontName = default.CharFontNameComplex = value
        elif tool == "Layout ▸ Margins ▸ Custom Margins…":
            if step["target"] == "Top / Bottom":
                self.style.TopMargin = self.style.BottomMargin = inches(value)
            elif step["target"] == "Left / Right":
                self.style.LeftMargin = self.style.RightMargin = inches(value)
            else:
                raise SystemExit(f"step {step['number']}: unknown margin target {step['target']!r}")
        elif tool == "Layout ▸ Size":
            assert value == "A4"
            self.style.Width, self.style.Height = 21000, 29700
        elif tool == "Insert ▸ Footer ▸ Edit Footer":
            self.style.FooterIsOn = True
            self.footer = self.style.FooterText
            self.footer_cursor = self.footer.createTextCursor()
        elif tool == "Home ▸ Paragraph ▸ Align Right":
            from com.sun.star.style.ParagraphAdjust import RIGHT

            self.footer.insertString(self.footer_cursor, value, False)
            self.footer_cursor.ParaAdjust = RIGHT
        elif tool == "Insert ▸ Page Number ▸ Current Position ▸ Plain Number":
            field = self.doc.createInstance("com.sun.star.text.TextField.PageNumber")
            field.NumberingType = 4  # ARABIC
            field.SubType = uno.Enum("com.sun.star.text.PageNumberType", "CURRENT")
            self.footer.insertTextContent(self.footer_cursor, field, False)
        elif tool == "Header & Footer ▸ Close Header and Footer":
            pass  # the cursor is back in the body; nothing to execute
        elif tool == "Home ▸ Styles ▸ Title":
            self.paragraph("Title", value)
        elif tool == "Home ▸ Styles ▸ Normal":
            self.paragraph("Standard", value)
        elif tool == "Home ▸ Styles ▸ Heading 1":
            self.paragraph("Heading 1", value)
        elif tool == "Insert ▸ Table ▸ Insert Table…":
            cols = int(value.split("Number of columns = ")[1].split(",")[0])
            rows = int(value.split("Number of rows = ")[1])
            self.table = self.doc.createInstance("com.sun.star.text.TextTable")
            self.table.initialize(rows, cols)
            self.text.insertControlCharacter(self.cursor, PARAGRAPH_BREAK, False)
            self.text.insertTextContent(self.cursor, self.table, False)
            self.first_paragraph = True  # the cursor now sits in the paragraph after the table
            self.row = 0
        elif tool == "Table Design ▸ Table Styles ▸ Table Grid":
            # Writer has no "Table Grid" style; it is Word's single 0.5 pt black grid.
            line = BorderLine2()
            line.OuterLineWidth, line.LineWidth, line.Color = 18, 18, 0
            for name in self.table.getCellNames():
                cell = self.table.getCellByName(name)
                for side in ("TopBorder", "BottomBorder", "LeftBorder", "RightBorder"):
                    setattr(cell, side, line)
        elif step["target"].startswith("แถว "):
            cells = value.split(" | ")
            for col, cell_value in enumerate(cells):
                self.table.getCellByPosition(col, self.row).setString(cell_value)
            self.row += 1
        elif not tool and step["section"] == "ยอดรวม":
            self.paragraph("Standard", value)
        elif tool == "File ▸ Save As":
            self.save(value, "MS Word 2007 XML")
        elif tool == "File ▸ Export ▸ Create PDF/XPS":
            self.save(value, "writer_pdf_Export")
        else:
            raise SystemExit(f"step {step['number']}: no UNO operation for {tool!r} / {action!r}")
        self.log.append({"step": step["number"], "tool": tool, "value": value})

    def save(self, name, filter_name):
        url = uno.systemPathToFileUrl(f"{self.outdir}/{name}")
        if filter_name == "writer_pdf_Export":
            self.doc.storeToURL(url, (prop("FilterName", filter_name),))
        else:
            self.doc.storeToURL(url, (prop("FilterName", filter_name), prop("Overwrite", True)))


def main():
    with open(sys.argv[1], encoding="utf-8") as handle:
        guide = json.load(handle)
    outdir = sys.argv[2]
    ctx = connect()
    desktop = ctx.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
    doc = desktop.loadComponentFromURL("private:factory/swriter", "_blank", 0, (prop("Hidden", True),))
    writer = Writer(doc)
    writer.outdir = outdir
    for step in guide["steps"]:
        writer.run(step)
    with open(f"{outdir}/replay-log.json", "w", encoding="utf-8") as handle:
        json.dump(writer.log, handle, ensure_ascii=False, indent=1)
    doc.close(True)
    print(f"followed {len(writer.log)} steps")


main()
