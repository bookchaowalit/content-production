"""Follow the LibreOffice Calc section of the marketing reverse guide in real Calc (UNO).

Each numbered guide step becomes one Calc operation:
  "File ▸ New ▸ Spreadsheet"              -> loadComponentFromURL(private:factory/scalc)
  "Ctrl+A ▸ Format ▸ Cells… ▸ Text"       -> .uno:SelectAll + NumberFormat = "@" on the selection
  "Tools ▸ AutoInput (off)"               -> .uno:AutoComplete with AutoComplete=False
  "พิมพ์ … ที่: A2; ค่า: `v`"              -> .uno:GoToCell A2 + .uno:EnterString v (typed input, parsed by Calc)
  "File ▸ Save As… ▸ Text CSV"            -> storeToURL, filter options comma / double quote / UTF-8
Usage (inside the container): python3 calc_follow.py GUIDE.md OUT.csv LOG.json
"""

import contextlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import uno
from com.sun.star.beans import PropertyValue

guide_path, out_csv, log_path = sys.argv[1:4]
text = Path(guide_path).read_text(encoding="utf-8")
section = text.split("#### LibreOffice Calc:", 1)[1].split("\n#### ", 1)[0]
steps = re.findall(r"^(\d+)\. (.*)$", section, flags=re.MULTILINE)


def prop(name, value):
    p = PropertyValue()
    p.Name, p.Value = name, value
    return p


proc = subprocess.Popen(["soffice", "--headless", "--norestore", "--accept=socket,host=127.0.0.1,port=2002;urp;"])
ctx = None
local = uno.getComponentContext()
resolver = local.ServiceManager.createInstanceWithContext("com.sun.star.bridge.UnoUrlResolver", local)
for _ in range(60):
    try:
        ctx = resolver.resolve("uno:socket,host=127.0.0.1,port=2002;urp;StarOffice.ComponentContext")
        break
    except Exception:  # noqa: BLE001 - UNO NoConnectException until soffice listens
        time.sleep(1)
smgr = ctx.ServiceManager
desktop = smgr.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
dispatcher = smgr.createInstanceWithContext("com.sun.star.frame.DispatchHelper", ctx)
doc = None
log = []
for number, line in steps:
    entry = {"step": int(number), "op": None}
    if "File ▸ New ▸ Spreadsheet" in line:
        doc = desktop.loadComponentFromURL("private:factory/scalc", "_blank", 0, (prop("Hidden", True),))
        entry["op"] = "new_spreadsheet"
    elif "Format ▸ Cells" in line and "Category: Text" in line:
        frame = doc.getCurrentController().getFrame()
        dispatcher.executeDispatch(frame, ".uno:SelectAll", "", 0, ())
        selection = doc.getCurrentController().getSelection()
        formats = doc.getNumberFormats()
        locale = uno.createUnoStruct("com.sun.star.lang.Locale")
        key = formats.queryKey("@", locale, False)
        if key == -1:
            key = formats.addNew("@", locale)
        selection.setPropertyValue("NumberFormat", key)
        entry.update(op="format_all_cells_text", format_key=key)
    elif "AutoInput" in line:
        frame = doc.getCurrentController().getFrame()
        dispatcher.executeDispatch(frame, ".uno:AutoComplete", "", 0, (prop("AutoComplete", False),))
        entry["op"] = "autoinput_off"
    elif "ที่: " in line and "ค่า: `" in line:
        match = re.search(r"ที่: ([A-Z]+[0-9]+); ค่า: `(.*)`$", line)
        cell, value = match.group(1), match.group(2)
        frame = doc.getCurrentController().getFrame()
        dispatcher.executeDispatch(frame, ".uno:GoToCell", "", 0, (prop("ToPoint", cell),))
        dispatcher.executeDispatch(frame, ".uno:EnterString", "", 0, (prop("StringName", value),))
        stored = doc.getSheets().getByIndex(0).getCellRangeByName(cell).getString()
        entry.update(op="type", cell=cell, typed=value, stored=stored)
    elif "Save As" in line and "Text CSV" in line:
        url = uno.systemPathToFileUrl(out_csv)
        doc.storeToURL(url, (prop("FilterName", "Text - txt - csv (StarCalc)"), prop("FilterOptions", "44,34,76,1,,0,false,false,false,false")))
        entry["op"] = "save_csv"
    else:
        entry["op"] = "unmapped"
        entry["line"] = line
    log.append(entry)

doc.close(True)
with contextlib.suppress(Exception):  # the bridge drops when soffice exits
    desktop.terminate()
proc.wait(timeout=30)
Path(log_path).write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps({"steps": len(log), "unmapped": [e for e in log if e["op"] == "unmapped"],
                  "typed_mismatch": [e for e in log if e["op"] == "type" and e["typed"] != e["stored"]]}, ensure_ascii=False, indent=1))
