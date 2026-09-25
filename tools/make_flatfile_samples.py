"""
Write a folder of deliberately awkward files for testing the flat-file connection.

    python tools/make_flatfile_samples.py                 # -> flatfile_samples/
    python tools/make_flatfile_samples.py some/other/dir

Each file exercises one thing real landed files get wrong. The table below
(printed when the script runs) says what the Flat Files page should show
for each one — see docs/FLAT_FILE_TESTING.md.
"""
from __future__ import annotations

import json
import os
import sys

# (file name, what should happen) — printed as the tester's checklist.
EXPECTED = [
    ("agents.csv", "AGENTS: 4 rows; A003 has an empty AGENT_NAME (a real NULL)"),
    ("claims_semicolon.csv", "CLAIMS_SEMICOLON: 3 rows, 3 columns (';' detected)"),
    ("regions.tsv", "REGIONS: 2 rows (tab-delimited)"),
    ("payments.psv", "PAYMENTS: 2 rows (pipe-delimited)"),
    ("windows_excel_export.csv", "WINDOWS_EXCEL_EXPORT: names show as José / Zoë, not garbage"),
    ("with_bom.csv", "WITH_BOM: first column is called ID (no stray characters)"),
    ("quoted.csv", "QUOTED: NOTE keeps 'has, a comma' and a two-line value"),
    ("na_is_data.csv", "NA_IS_DATA: STATE shows 'NA' (text), only row 3 is NULL"),
    ("messy headers.csv", "MESSY_HEADERS: columns ID, Name, name_2"),
    ("2026 feed.csv", "T_2026_FEED: table name gets a T_ prefix"),
    ("nested.json", "NESTED: TAGS / ADDRESS shown as JSON text"),
    ("events.jsonl", "EVENTS: 3 rows"),
    ("header_only.csv", "HEADER_ONLY: 0 rows, 2 columns"),
    ("empty.csv", "listed under 'could not be loaded' — the file is empty"),
    ("broken.json", "listed under 'could not be loaded' — the other files still load"),
    ("book.xlsx", "BOOK_MOTOR (2 rows) and BOOK_HOME (1 row) — one table per sheet"),
]


def write(folder: str, name: str, content: str | bytes) -> None:
    mode = "wb" if isinstance(content, bytes) else "w"
    with open(os.path.join(folder, name), mode, **({} if mode == "wb" else {"newline": ""})) as f:
        f.write(content)


def main(folder: str) -> None:
    os.makedirs(folder, exist_ok=True)
    write(folder, "agents.csv",
          "AGENT_ID,AGENT_NAME,REGION,JOINED\n"
          "A001,Asha Rao,South,2024-01-15\n"
          "A002,Vikram Das,North,2024-03-02\n"
          "A003,,West,2025-07-19\n"
          "A004,Meera Iyer,South,2025-11-30\n")
    write(folder, "claims_semicolon.csv", "CLAIM_ID;POLICY_ID;AMOUNT\nCL1;P1001;100.5\nCL2;P1002;200\nCL3;P1003;75.25\n")
    write(folder, "regions.tsv", "REGION\tMANAGER\nSouth\tLakshmi\nNorth\tArjun\n")
    write(folder, "payments.psv", "PAYMENT_ID|AMOUNT\nPAY1|10.00\nPAY2|20.50\n")
    write(folder, "windows_excel_export.csv", "ID,NAME\n1,José\n2,Zoë\n".encode("cp1252"))
    write(folder, "with_bom.csv", "ID,NAME\n1,Asha\n".encode("utf-8-sig"))
    write(folder, "quoted.csv", 'ID,NOTE\n1,"has, a comma"\n2,"two\nlines"\n')
    write(folder, "na_is_data.csv", "ID,STATE\n1,NA\n2,KA\n3,\n")
    write(folder, "messy headers.csv", " ID , Name ,name\n1,a,b\n")
    write(folder, "2026 feed.csv", "K,V\n1,x\n")
    write(folder, "nested.json", json.dumps([
        {"ID": 1, "TAGS": ["motor", "renewal"], "ADDRESS": {"CITY": "Pune"}},
        {"ID": 2, "TAGS": [], "ADDRESS": {"CITY": "Kochi"}},
    ]))
    write(folder, "events.jsonl", '{"ID": 1, "E": "open"}\n{"ID": 2, "E": "close"}\n{"ID": 3, "E": "open"}\n')
    write(folder, "header_only.csv", "A,B\n")
    write(folder, "empty.csv", "")
    write(folder, "broken.json", "{this is not json")

    try:
        import pandas as pd
        with pd.ExcelWriter(os.path.join(folder, "book.xlsx")) as xw:
            pd.DataFrame({"POLICY_ID": ["P1", "P2"], "PREMIUM": [100, 200]}) \
                .to_excel(xw, sheet_name="Motor", index=False)
            pd.DataFrame({"POLICY_ID": ["P3"], "PREMIUM": [300]}) \
                .to_excel(xw, sheet_name="Home", index=False)
    except ImportError:
        print("  (skipped book.xlsx — pip install openpyxl to include the Excel sample)")

    width = max(len(n) for n, _ in EXPECTED)
    print(f"Wrote {len(EXPECTED)} sample files to {os.path.abspath(folder)}\n")
    print("What the Flat Files page should show:")
    for name, expected in EXPECTED:
        print(f"  {name:<{width}}  {expected}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "flatfile_samples")
