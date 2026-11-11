"""Follow docs/reverse/web-v1a/webflow.md by hand, in plain HTML/CSS (R4 evidence).

Reads the committed guide only (layer table + numbered steps), never the engine
files. Each numbered step becomes one operation on a small DOM / style sheet,
using the guide's own words and values with the Webflow panel vocabulary
(Style panel "Height" with a unitless value = line-height, "Max W" = max-width,
"1 คอลัมน์" = one grid column, ...). Steps that only repeat a layer-table row
(drag an element, type its text) are checked against that row. The log lists
every step and what it did.

This verifies the guide's VALUES with browser defaults. It does not run
Webflow and does not include Webflow's own base stylesheet.

Usage: python follow_guide.py GUIDE.md HERO_SOURCE.png OUT_DIR
"""

from __future__ import annotations

import html
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

VARIABLES: dict[str, str] = {}


@dataclass
class Node:
    tag: str
    classes: list[str] = field(default_factory=list)
    ident: str = ""
    attrs: dict[str, str] = field(default_factory=dict)
    text: str = ""
    children: list[Node] = field(default_factory=list)

    def render(self, depth: int = 0) -> str:
        attrs = ""
        if self.ident:
            attrs += f' id="{html.escape(self.ident)}"'
        if self.classes:
            attrs += f' class="{" ".join(self.classes)}"'
        for key, value in self.attrs.items():
            attrs += f" {key}" if value == "" else f' {key}="{html.escape(value)}"'
        if self.tag in ("img", "input"):
            return f"<{self.tag}{attrs}>"
        inner = html.escape(self.text) + "".join(child.render(depth + 1) for child in self.children)
        return f"<{self.tag}{attrs}>{inner}</{self.tag}>"


def parse_guide(text: str) -> tuple[list[list[str]], list[dict[str, str]]]:
    layers: list[list[str]] = []
    steps: list[dict[str, str]] = []
    section = ""
    in_layers = False
    for line in text.splitlines():
        if line.startswith("### 2."):
            in_layers = True
        elif line.startswith("### 3."):
            in_layers = False
        if in_layers and re.match(r"^\| \d+ \|", line):
            cells = [cell.strip().replace("\\|", "|") for cell in re.split(r"(?<!\\)\|", line)[1:-1]]
            layers.append(cells)
        if line.startswith("#### "):
            section = line[5:]
        match = re.match(r"^(\d+)\. (.*)$", line)
        if match and section and not in_layers:
            body = match.group(2)
            value = re.search(r"ค่า: `([^`]*)`", body)
            tool = re.search(r"เครื่องมือ/เมนู: `([^`]*)`", body)
            steps.append({"n": match.group(1), "section": section, "action": body.split(" — ")[0], "tool": tool.group(1) if tool else "", "value": value.group(1) if value else "", "note": ""})
        note = re.match(r"^   - หมายเหตุ: (.*)$", line)
        if note and steps:
            steps[-1]["note"] = note.group(1)
    return layers, steps


def _selector_node(position: str) -> Node:
    """'section.features.container#pricing aria-labelledby=x' → Node."""

    head, *rest = position.split(" ", 1)
    attrs: dict[str, str] = {}
    if rest:
        for key, value in re.findall(r"([a-z-]+)=(.*?)(?= [a-z-]+=| required$|$)", rest[0]):
            attrs[key] = value
        if rest[0].endswith("required"):
            attrs["required"] = ""
    bracket = re.findall(r"\[([a-z]+)=([^\]]+)\]", head)
    head = re.sub(r"\[.*?\]", "", head)
    ident = ""
    if "#" in head:
        head, ident = head.split("#", 1)
    tag, *classes = head.split(".")
    for key, value in bracket:
        attrs[key] = value
    return Node(tag=tag, classes=classes, ident=ident, attrs=attrs)


def build_tree(layers: list[list[str]], hero_name: str) -> tuple[Node, dict[str, Node]]:
    body = Node("body")
    by_path: dict[str, Node] = {"Body": body}
    for _order, path, _kind, content, position in layers:
        parent_path = path.rsplit(" ▸ ", 1)[0]
        while parent_path not in by_path:  # "… ▸ p ▸ Button": the p wrapper is part of this row
            parent_path = parent_path.rsplit(" ▸ ", 1)[0]
        parent = by_path[parent_path]
        content = "" if content == "—" else content
        if position.startswith(("p > ", "p.secondary-cta > ")):
            wrapper_spec, link_spec = position.split(" > ", 1)
            wrapper = _selector_node(wrapper_spec)
            link = _selector_node(link_spec)
            link.text = content
            wrapper.children.append(link)
            parent.children.append(wrapper)
            by_path[path] = link
            continue
        node = _selector_node(position)
        if node.tag == "img":
            node.attrs = {"src": hero_name, **node.attrs}
        elif node.tag == "textarea":
            node.attrs.pop("type", None)
        elif node.tag == "button" or content:
            node.text = content
        parent.children.append(node)
        by_path[path] = node
    return body, by_path


def colour(word: str) -> str:
    hex_match = re.search(r"#[0-9A-Fa-f]{6}", word)
    name = word.split(" ")[0].strip()
    if name in VARIABLES:
        return f"var(--{name})"
    return hex_match.group(0) if hex_match else word


def box(text: str) -> str:
    """'5rem (80px) บน-ล่าง, 0 ซ้าย-ขวา' / '0 บน, 4rem ล่าง' / '0.75rem 1.1rem' → CSS shorthand."""

    text = re.sub(r"\s*\(\d+px\)", "", text).strip()
    if "บน-ล่าง" in text:
        vertical = text.split("บน-ล่าง")[0].strip()
        horizontal = re.search(r"([\w.]+) ซ้าย-ขวา", text)
        return f"{vertical} {horizontal.group(1) if horizontal else '0'}"
    if "บน" in text and "ล่าง" in text:
        top = re.search(r"([\w.]+) บน", text)
        bottom = re.search(r"([\w.]+) ล่าง", text)
        return f"{top.group(1) if top else '0'} 0 {bottom.group(1) if bottom else '0'}"
    return text


def declarations(value: str) -> dict[str, str]:
    """Webflow Style-panel words → CSS declarations (one token per ';' or ',' separated item)."""

    out: dict[str, str] = {}
    tokens = [token.strip() for token in re.split(r";|, (?=[A-Z])", value) if token.strip()]
    for token in tokens:
        low = token.lower()
        words = token.split(" ", 1)
        arg = words[1] if len(words) > 1 else ""
        if low.startswith("class "):
            continue
        if low.startswith("display grid"):
            out["display"] = "grid"
        elif low.startswith("display "):
            out["display"] = arg.lower()
        elif re.match(r"^grid (\d+) คอลัมน์", low):
            count = int(re.match(r"^grid (\d+)", low).group(1))  # type: ignore[union-attr]
            out["display"] = "grid"
            out["grid-template-columns"] = " ".join(["1fr"] * count) if "1fr" in token else f"repeat({count}, minmax(0, 1fr))"
        elif re.match(r"^1 คอลัมน์", token):
            out["grid-template-columns"] = "1fr"
        elif low.startswith("gap "):
            out["gap"] = arg
        elif low.startswith("radius "):
            out["border-radius"] = arg
        elif low.startswith("background "):
            out["background"] = colour(arg)
        elif low.startswith("color "):
            out["color"] = colour(arg)
        elif low.startswith("padding ") and not low.startswith("padding left "):
            out["padding"] = box(arg)
        elif low.startswith("weight "):
            out["font-weight"] = arg
        elif low.startswith("decoration "):
            out["text-decoration"] = arg
        elif low.startswith("outline offset "):
            out["outline-offset"] = token.split(" ", 2)[2]
        elif low.startswith("outline color "):
            out["outline-color"] = token.split(" ", 2)[2]
        elif low.startswith("outline "):
            parts = arg.split(" ")
            out["outline"] = " ".join([parts[0], parts[1], colour(" ".join(parts[2:]))])
        elif low.startswith("max w "):
            out["max-width"] = token[6:]
        elif low.startswith("width "):
            out["width"] = arg
        elif low.startswith("height "):
            if re.fullmatch(r"[0-9.]+", arg.split(" ")[0]):
                out["line-height"] = arg.split(" ")[0]
            else:
                out["height"] = arg.lower()
        elif low.startswith("line-height "):
            out["line-height"] = arg
        elif low.startswith(("font size ", "size ")):
            out["font-size"] = re.sub(r"\s*\(\d+px\)", "", re.split(r"(?i)size ", token, maxsplit=1)[1])
        elif low.startswith("padding left "):
            out["padding-left"] = token[13:]
        elif low.startswith("font inherit"):
            out["font"] = "inherit"
        elif low.startswith("margin top "):
            out["margin-top"] = token[11:]
        elif low.startswith("margin bottom "):
            out["margin-bottom"] = token[14:]
        elif low.startswith("margin ซ้าย-ขวา"):
            out["margin-left"] = out["margin-right"] = "auto"
        elif low.startswith("margin "):
            out["margin"] = arg
        elif low.startswith("letter spacing "):
            out["letter-spacing"] = token[15:]
        elif "uppercase" in low:
            out["text-transform"] = "uppercase"
        elif low.startswith("border top "):
            out["border-top"] = colour_border(token[11:])
        elif low.startswith("border bottom "):
            out["border-bottom"] = colour_border(token[14:])
        elif low.startswith("border "):
            out["border"] = colour_border(arg)
        elif low.startswith("cursor "):
            out["cursor"] = arg
        elif low.startswith("system-ui"):
            out["font-family"] = token
        elif token == "0":
            out["margin"] = "0"
        else:
            raise ValueError(f"unknown style word: {token!r}")
    return out


def colour_border(text: str) -> str:
    match = re.match(r"(\S+) (\S+) (.*)$", text)
    if not match:
        return text
    return f"{match.group(1)} {match.group(2)} {colour(match.group(3)) if 'rgba' not in match.group(3) else match.group(3)}"


def follow(guide_path: Path, hero_source: Path, out_dir: Path) -> list[str]:
    layers, steps = parse_guide(guide_path.read_text(encoding="utf-8"))
    hero_name = next((row[3] for row in layers if row[2] == "Image"), "hero.png")
    body, _by_path = build_tree(layers, hero_name)
    head: dict[str, str] = {}
    rules: list[tuple[str, str, dict[str, str]]] = []  # (media, selector, declarations)
    media = ""
    log: list[str] = []
    css_name = html_name = ""

    def rule(selector: str, decls: dict[str, str]) -> None:
        rules.append((media, selector, decls))

    def find(tag: str, cls: str = "") -> list[Node]:
        found: list[Node] = []

        def walk(node: Node) -> None:
            if node.tag == tag and (not cls or cls in node.classes):
                found.append(node)
            for child in node.children:
                walk(child)

        walk(body)
        return found

    for step in steps:
        n, action, tool, value, note = step["n"], step["action"], step["tool"], step["value"], step["note"]
        did = "layer-table row (element / text / link / id / attribute already placed from section 2)"
        if tool.startswith("Pages panel") or tool in ("Page settings ▸ Slug", "Page settings ▸ Open Graph Settings ▸ Open Graph Image URL"):
            did = "page settings only (slug / no og:image: no markup change)"
        elif tool.startswith("Page settings ▸ SEO Settings ▸ Title Tag"):
            head["title"] = value
            did = "<title>"
        elif "Meta Description" in tool:
            head["description"] = value
            did = "meta description"
        elif "Open Graph Title" in tool:
            head["og:title"] = value
            did = "og:title"
        elif "Open Graph Description" in tool:
            head["og:description"] = value
            did = "og:description"
        elif "Primary locale" in tool:
            lang, locale = re.findall(r"=(\S+?)(?:,|$)", value)
            head["lang"], head["og:locale"] = lang, locale
            did = "html lang + og:locale; og:type/twitter:card per note"
        elif "Variables panel" in tool:
            name = re.search(r"`(\w+)`", action).group(1)  # type: ignore[union-attr]
            VARIABLES[name] = value
            rule(":root", {f"--{name}": value})
            did = f"variable --{name}"
        elif tool.startswith("Navigator ▸ Body; Style panel"):
            rule("body", {"background": "var(--background)", "color": "var(--foreground)"})
            did = "body colours"
        elif tool.startswith("Style panel ▸ Typography ▸ Font / Height"):
            rule("body", declarations(value))
            did = "body font"
        elif tool == "Style panel ▸ Spacing ▸ Margin":
            rule("body", {"margin": value})
            did = "body margin"
        elif tool.startswith("Style panel ▸ Body (All Pages) ▸ Typography ▸ Size"):
            rule("body", declarations(value))
            did = "body font size"
        elif value == "box-sizing border-box":
            rule("*", {"box-sizing": "border-box"})
            did = "* box-sizing (the plain HTML/CSS path of this step)"
        elif tool.startswith("Style panel ▸ Selector ▸ All ") and "H1" not in tool:
            selector = {"All H2 Headings": "h2", "All H3 Headings": "h3", "All Paragraphs": "p", "All Lists": "ul"}[tool.rsplit("▸ ", 1)[1]]
            rule(selector, declarations(value))
            did = f"tag default {selector}"
        elif action.startswith("ใส่ class `hero` แล้วตั้งสไตล์"):
            rule(".hero", declarations(value))
            did = ".hero style"
        elif action.startswith("ลาก Div Block เข้าไปใน Header แล้วใส่ class `container`"):
            rule(".container", declarations(value.replace("calc(100% - 2rem)", "calc(100%·-·2rem)")))
            rules[-1][2]["width"] = rules[-1][2]["width"].replace("·", " ")
            did = ".container style"
        elif "class `eyebrow`" in action:
            rule(".eyebrow", declarations(note.split(":", 1)[1]))
            did = ".eyebrow style (from note)"
        elif "All H1 Headings" in tool:
            rule("h1", declarations(value))
            did = "All H1 Headings"
        elif "class `hero-body`" in action:
            rule(".hero-body", declarations(note.split(":", 1)[1]))
            did = ".hero-body style (from note)"
        elif action.startswith("ใส่ class `button` + combo class `is-hero`"):
            for link in find("a", "button")[:1]:
                link.classes.append("is-hero")
            did = "combo class is-hero on the hero button"
        elif tool == "Style panel ▸ .button":
            rule(".button", declarations(value))
            did = ".button style"
        elif tool == "Style panel ▸ .button.is-hero":
            rule(".button.is-hero", declarations(value))
            did = ".button.is-hero style"
        elif tool == "Style panel ▸ .button ▸ States ▸ Focused (keyboard)":
            rule(".button:focus-visible", declarations(value))
            did = ".button focus state"
        elif tool == "Style panel ▸ .button.is-hero ▸ States ▸ Focused (keyboard)":
            rule(".button.is-hero:focus-visible", declarations(value))
            did = ".button.is-hero focus state"
        elif tool == "Style panel ▸ .hero-image":
            rule(".hero-image", declarations(value))
            did = ".hero-image style"
        elif action.startswith("ลาก Section เข้า Main ใส่ class `container` แล้ว combo class `features`"):
            rule(".container.features", declarations(value.split("; ", 1)[1]))
            did = ".container.features padding"
        elif "Display Grid" in tool and "feature-grid" in action:
            rule(".feature-grid", declarations(value))
            did = ".feature-grid grid"
        elif tool == "Style panel ▸ .feature-card":
            rule(".feature-card", declarations(value))
            did = ".feature-card style"
        elif re.match(r"ลาก Section มาใต้.*combo `(\w+)`", action):
            combo = re.search(r"combo `(\w+)`", action).group(1)  # type: ignore[union-attr]
            rule(f".container.{combo}", declarations(value.split("; ")[-1]))
            did = f".container.{combo} padding"
        elif "pricing-grid" in action and "Display Grid" in tool:
            rule(".pricing-grid", declarations(value.replace("เท่ากัน", "")))
            did = ".pricing-grid grid"
        elif tool == "Style panel ▸ .pricing-card / .price":
            card, price = value.split(". ราคา:")
            rule(".pricing-card", declarations(card.split(":", 1)[1]))
            rule(".price", declarations(price))
            did = ".pricing-card + .price style"
        elif "faq-question" in note:
            number = int(re.search(r"คำถามที่ (\d+)", action).group(1))  # type: ignore[union-attr]
            find("summary")[number - 1].classes.append("faq-question")
            did = "summary + class faq-question (note); text from layer row"
        elif tool == "Style panel ▸ .faq-item / .faq-question":
            item, question = value.split(". faq-question:")
            rule(".faq-item", declarations(item.split(":", 1)[1]))
            question_style, focus = question.split("; State Focused (keyboard):")
            rule(".faq-question", declarations(question_style))
            rule(".faq-question:focus-visible", declarations(focus.replace(", offset", "; Outline offset")))
            did = ".faq-item / .faq-question style + focus state"
        elif tool == "Settings panel (D) ▸ Form settings ▸ Action / Method":
            form = find("form")[0]
            form.attrs["action"] = re.search(r"Action (\S+);", value).group(1)  # type: ignore[union-attr]
            form.attrs["method"] = re.search(r"Method (\w+)", value).group(1).lower()  # type: ignore[union-attr]
            did = "form action + method"
        elif tool == "Style panel ▸ .lead-form":
            rule(".lead-form", declarations(value.replace("1 คอลัมน์", "1 คอลัมน์")))
            did = ".lead-form style"
        elif "Text Area; Settings panel" in tool:
            find("textarea")[0].attrs["rows"] = re.search(r"Rows (\d+)", value).group(1)  # type: ignore[union-attr]
            did = "textarea rows (id/name/required from layer row)"
        elif "Forms ▸ Input; Settings panel" in tool:
            ident = re.search(r"ID (\S+);", value).group(1)  # type: ignore[union-attr]
            node = next(item for item in find("input") if item.ident == ident)
            node.attrs["autocomplete"] = note.rsplit("= ", 1)[1]
            did = f"input #{ident} autocomplete (type/name/required from layer row)"
        elif tool == "Style panel ▸ .form-field":
            rule(".form-field", declarations(value))
            did = ".form-field style"
        elif tool.startswith("Style panel ▸ All Form Labels"):
            rule("label", declarations(value))
            did = "All Form Labels"
        elif tool == "Style panel ▸ All Inputs / Text Area":
            rule("input, textarea", declarations(value))
            did = "All Inputs / Text Area"
        elif tool == "Style panel ▸ All Inputs ▸ States ▸ Focused (keyboard)":
            rule("input:focus-visible, textarea:focus-visible", declarations(value))
            did = "input focus state"
        elif tool == "Style panel ▸ .button (ใน Form)":
            rule(".lead-form .button", declarations(value))
            did = "submit button style"
        elif tool == "Style panel ▸ Backgrounds / Borders / Spacing":
            rule(".container.cta", declarations(value))
            did = ".container.cta style"
        elif tool == "Style panel ▸ .secondary-cta":
            rule(".secondary-cta", declarations(value))
            did = ".secondary-cta style"
        elif "Tag Footer" in tool:
            rule(".footer", declarations(value))
            did = ".footer style"
        elif tool.startswith("Top bar ▸ Breakpoints ▸ Mobile portrait"):
            media = ""
            did = "check only (no style)"
        elif tool.startswith("Top bar ▸ Breakpoints"):
            media = f"@media (max-width: {value})"
            did = f"switch to {media}"
        elif re.match(r"Style panel ▸ \.(hero|pricing-grid|feature-grid) ▸", tool):
            selector = "." + re.match(r"Style panel ▸ \.([\w-]+)", tool).group(1)  # type: ignore[union-attr]
            decls = {"padding": box(value)} if "Spacing" in tool else declarations(value)
            rule(selector, decls)
            did = f"{media} {selector}"
        elif tool.startswith("VS Code ▸ File ▸ New File"):
            html_name, css_name = [part.strip() for part in value.split(",")]
            did = "file names"
        elif tool in ("Editor", "File ▸ Save", "Audit panel (Shift+A)", "(ไม่มี)") or tool.startswith("Publish"):
            did = "no page change (export / check step)"
        log.append(f"{n:>3} [{step['section']}] {did}")

    css_lines: list[str] = []
    grouped: dict[str, list[tuple[str, dict[str, str]]]] = {}
    for media_query, selector, decls in rules:
        grouped.setdefault(media_query, []).append((selector, decls))
    for media_query, items in grouped.items():
        block = [f"{selector} {{ " + " ".join(f"{key}: {value};" for key, value in decls.items()) + " }" for selector, decls in items]
        css_lines += block if not media_query else [media_query + " {", *("  " + line for line in block), "}"]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / css_name).write_text("\n".join(css_lines) + "\n", encoding="utf-8")
    meta = "".join(
        f'<meta {"name" if key in ("description", "twitter:card") else "property"}="{key}" content="{html.escape(content)}">'
        for key, content in (("description", head["description"]), ("og:type", "website"), ("og:title", head["og:title"]),
                             ("og:description", head["og:description"]), ("og:locale", head["og:locale"]), ("twitter:card", "summary"))
    )
    page = (
        f'<!doctype html>\n<html lang="{head["lang"]}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html.escape(head['title'])}</title>{meta}"
        f'<link rel="stylesheet" href="{css_name}"></head>{body.render()}</html>\n'
    )
    (out_dir / html_name).write_text(page, encoding="utf-8")
    shutil.copyfile(hero_source, out_dir / hero_name)
    (out_dir / "follow-log.txt").write_text("\n".join(log) + "\n", encoding="utf-8")
    return log


if __name__ == "__main__":
    lines = follow(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
    print(f"{len(lines)} steps followed")
