# marketing-v1a — reverse guide verification

Rule: [reverse guides spec R4](../../superpowers/specs/2026-11-12-reverse-guides-and-flow-labels-design.md).

| Date | Guide | Program | Followed by | Method | Result |
|---|---|---|---|---|---|
| 2026-11-12 | [meta-business-suite.md](meta-business-suite.md), section "LibreOffice Calc: ไฟล์ bulk CSV" (steps 36–108) | LibreOffice Calc 7.4.7.2 (Debian bookworm, headless, Docker) | Claude agent via Python UNO ([calc_follow.py](evidence/calc_follow.py)): the script reads the committed guide and runs each numbered step as one Calc operation — new spreadsheet, select all + number format Text, AutoInput off, `.uno:GoToCell` + `.uno:EnterString` with the guide's exact cell and value (typed input, parsed by Calc), Save As Text CSV (comma, double quote, UTF-8) | Calc's CSV vs the engine `.bulk.csv` from `python -m marketing_production.cli build --input campaign.json` (in `examples/marketing/`), both read with Python `csv`, compared cell by cell ([compare_csv.py](evidence/compare_csv.py)) | **Pass** — 98/98 cells equal, 7 rows × 14 columns |
| — | same guide, channel sections (Mailchimp, Meta Business Suite, LINE OA Manager, Canva) and the approval section | web UIs | not followed | — | **Pending owner** (below) |

## 2026-11-12 run (LibreOffice Calc)

Evidence: [engine CSV](evidence/engine.bulk.csv) · [Calc CSV](evidence/calc.bulk.csv) ·
[step log](evidence/calc-steps.json) (73 steps, 0 unmapped, every typed value read back
unchanged from its cell) · [comparison](evidence/compare.json).

- Engine CSV sha256 `a45d08b5…b7bb` matches the golden in `meta-business-suite.md`;
  the package JSON sha256 `92b045b1…4f2b` matches its golden too.
- Calc's file differs only in line endings: Calc writes `LF`, the engine writes
  `CRLF` (RFC 4180). After removing `CR` the two files are byte-identical,
  including quoting.
- The formula-escaped cell `I7` (`'- ลาเต้ร้อน …`, copy starting with `-`) kept its
  apostrophe because step 37 formats every cell as Text first; Thai text,
  `@mentions`, `#hashtags`, URLs with `&` and dates such as `2026-12-01T09:00`
  stayed literal (no date or number conversion).

### Findings fixed in the guide

1. **Save As step markup.** The first render put the delimiters in nested
   backticks (`` `,` `` inside a code span), which broke the Markdown of step 108.
   The guide builder now writes `Field delimiter: comma (,), String delimiter:
   double quote (")`.

### Not covered by this run

- **Pending owner — web UIs.** No Meta, Mailchimp, LINE OA or Canva account is
  available to the agent. To verify, the owner (or someone with a test page /
  audience) follows steps 1–35 of `meta-business-suite.md` with
  `examples/marketing/campaign.json`, saves every item as a **draft only**, and
  checks: each copy is pasted character for character (counts in the steps),
  each tracked link equals the guide's value, the Canva export is 1080×1350 px,
  the Mailchimp subject / preview text match, and nothing is published,
  scheduled, sent, or boosted. Record a row here with the program and date.
- Importing the CSV into a scheduler (Hootsuite Bulk Composer, Meta Planner):
  the sheet is a generic interchange format, not a platform template (P2 in
  `docs/parity/marketing.md`).
- Typing through the Calc GUI keyboard path (the run used `EnterString`, which
  parses input like the cell editor but does not show dialogs).
