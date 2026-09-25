"""
Flat-file connector.

Treats a folder of delimited, Excel, JSON or Parquet files as a small
read-only database: every file is loaded into an in-memory SQLite database
as one table (named after the file), and test cases query it with the same
``:named`` SQL they use everywhere else::

    SELECT CLAIM_ID, CLAIM_AMOUNT FROM CLAIMS WHERE CLAIM_STATUS = :status

That is what lets a SOURCE_TARGET_COMPARE test reconcile a landed file
against the Snowflake or MSSQL table it was loaded into, with no staging
step in between. SQLite speaks ``:named`` natively, so binding is a
pass-through.

Files are read when the connection is first used, so a new or replaced file
is picked up by the next run (or immediately, after ``refresh()``).
"""
from __future__ import annotations

import csv
import os
import re
import sqlite3
import threading
from datetime import date, datetime
from decimal import Decimal

from src.connectors.base import Connector, extract_params

DELIMITED_EXTENSIONS = (".csv", ".tsv", ".txt", ".psv", ".dat")
EXCEL_EXTENSIONS = (".xlsx", ".xls")
JSON_EXTENSIONS = (".json", ".jsonl")
PARQUET_EXTENSIONS = (".parquet",)
SUPPORTED_EXTENSIONS = DELIMITED_EXTENSIONS + EXCEL_EXTENSIONS + JSON_EXTENSIONS + PARQUET_EXTENSIONS

# Extensions whose delimiter is fixed by convention; everything else is sniffed.
_FIXED_DELIMITERS = {".tsv": "\t", ".psv": "|"}
_SNIFF_DELIMITERS = ",;\t|"

# Tried in order when the configured encoding can't decode a file. cp1252 is
# what Excel on Windows writes; latin-1 decodes any byte, so it always succeeds.
_FALLBACK_ENCODINGS = ("utf-8-sig", "cp1252", "latin-1")


class FlatFileConnector(Connector):
    kind = "flatfile"

    def __init__(self, profile):
        super().__init__(profile)
        self.tables: list[dict] = []        # one entry per loaded table
        self.load_errors: list[dict] = []   # files that could not be read
        # The Streamlit app shares one connector across browser sessions, and a
        # sqlite3 connection must not run two statements at once.
        self._lock = threading.RLock()

    # ---- lifecycle -----------------------------------------------------
    def _connect(self):
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.tables, self.load_errors = [], []
        taken: set[str] = set()
        for path in list_data_files(self.profile.options["path"]):
            try:
                frames = self._read(path)
            except Exception as exc:
                self.load_errors.append({"file": os.path.basename(path),
                                         "error": f"{type(exc).__name__}: {exc}"})
                continue
            for suffix, df in frames:
                name = _unique(table_name_for(path, suffix), taken)
                df.columns = _clean_columns(df.columns)
                try:
                    _to_sqlite_values(df).to_sql(name, conn, index=False)
                except Exception as exc:
                    taken.discard(name)
                    label = os.path.basename(path) + (f" [{suffix}]" if suffix else "")
                    self.load_errors.append({"file": label,
                                             "error": f"{type(exc).__name__}: {exc}"})
                    continue
                self.tables.append({
                    "table": name,
                    "file": os.path.basename(path),
                    "sheet": suffix or "",
                    "rows": len(df),
                    "columns": list(df.columns),
                })
        return conn

    def refresh(self) -> None:
        """Drop what was loaded; the next query re-reads the files from disk."""
        with self._lock:
            self.close()

    def query(self, sql, params=None):
        with self._lock:
            return super().query(sql, params)

    def execute(self, sql, params=None):
        with self._lock:
            return super().execute(sql, params)

    def ensure_loaded(self) -> None:
        self.connection  # noqa: B018 — reading the property triggers the load

    # ---- reading -------------------------------------------------------
    def _read(self, path: str) -> list[tuple[str, "object"]]:
        """Return [(table-name suffix, DataFrame)] for one file."""
        import pandas as pd

        o = self.profile.options
        ext = os.path.splitext(path)[1].lower()
        dtype = str if o.get("all_text") else None

        if ext in DELIMITED_EXTENSIONS:
            if os.path.getsize(path) == 0:
                raise ValueError("the file is empty")
            encoding = detect_encoding(path, o.get("encoding") or "utf-8")
            delimiter = o.get("delimiter") or _FIXED_DELIMITERS.get(ext) \
                or sniff_delimiter(path, encoding)
            return [("", pd.read_csv(
                path, sep=delimiter, encoding=encoding, dtype=dtype,
                header=0 if o.get("header", True) else None, skipinitialspace=True,
                decimal=o.get("decimal") or ".",
                # Only a genuinely empty cell is NULL. Text such as "NA", "null"
                # or "N/A" is data a validation should see, not a missing value.
                keep_default_na=False, na_values=[""],
            ))]
        if ext in EXCEL_EXTENSIONS:
            sheets = pd.read_excel(path, sheet_name=None, dtype=dtype,
                                   header=0 if o.get("header", True) else None)
            if len(sheets) == 1:
                return [("", next(iter(sheets.values())))]
            return [(sheet, df) for sheet, df in sheets.items()]
        if ext in JSON_EXTENSIONS:
            return [("", pd.read_json(path, lines=ext == ".jsonl", dtype=dtype or True))]
        if ext in PARQUET_EXTENSIONS:
            df = pd.read_parquet(path)
            return [("", df.astype(str) if dtype else df)]
        raise ValueError(f"Unsupported file type '{ext}'")

    # ---- binding -------------------------------------------------------
    def bind(self, sql: str, params: dict):
        self._check_params(sql, params)
        used = set(extract_params(sql))
        args = {k: _adapt(v) for k, v in params.items() if k in used}
        return sql, (args or None)

    # ---- introspection -------------------------------------------------
    def describe(self) -> list[dict]:
        """The tables this folder exposes, loading the files if needed."""
        self.ensure_loaded()
        return list(self.tables)


# ---------------------------------------------------------------------------
# Helpers — also used by the Flat Files page for uploads.
# ---------------------------------------------------------------------------
def list_data_files(path: str) -> list[str]:
    """Supported files under `path` (a folder, or a single file), sorted by name."""
    if os.path.isfile(path):
        return [path] if path.lower().endswith(SUPPORTED_EXTENSIONS) else []
    if not os.path.isdir(path):
        raise FileNotFoundError(f"Flat-file folder does not exist: {path}")
    return sorted(
        os.path.join(path, f) for f in os.listdir(path)
        if f.lower().endswith(SUPPORTED_EXTENSIONS) and not f.startswith((".", "~$"))
        and os.path.isfile(os.path.join(path, f))
    )


def table_name_for(path: str, suffix: str = "") -> str:
    """claims.csv -> CLAIMS;  2026 Q1 book.xlsx + sheet 'Motor' -> T_2026_Q1_BOOK_MOTOR."""
    stem = os.path.splitext(os.path.basename(path))[0]
    raw = f"{stem}_{suffix}" if suffix else stem
    name = re.sub(r"\W+", "_", raw).strip("_").upper() or "FILE"
    return f"T_{name}" if name[0].isdigit() else name


def safe_filename(name: str) -> str:
    """Strip any directory part and odd characters from an uploaded file's name."""
    base = os.path.basename(name.replace("\\", "/"))
    stem, ext = os.path.splitext(base)
    if ext.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"'{base}' is not a supported file type "
                         f"({', '.join(SUPPORTED_EXTENSIONS)}).")
    stem = re.sub(r"[^\w\- ]+", "_", stem).strip(" ._") or "upload"
    return f"{stem}{ext.lower()}"


def detect_encoding(path: str, preferred: str = "utf-8") -> str:
    """`preferred` if it decodes the whole file, else the first fallback that does."""
    with open(path, "rb") as f:
        raw = f.read()
    for enc in (preferred,) + _FALLBACK_ENCODINGS:
        try:
            raw.decode(enc)
            return enc
        except (UnicodeDecodeError, LookupError):
            continue
    return "latin-1"


def sniff_delimiter(path: str, encoding: str = "utf-8") -> str:
    with open(path, "r", encoding=encoding, errors="replace", newline="") as f:
        sample = f.read(64 * 1024)
    try:
        return csv.Sniffer().sniff(sample, delimiters=_SNIFF_DELIMITERS).delimiter
    except csv.Error:
        return ","


def _clean_columns(columns) -> list[str]:
    """Trim header whitespace, name blank headers, and de-duplicate case-insensitively."""
    out, seen = [], set()
    for i, c in enumerate(columns, start=1):
        name = str(c).strip() or f"COLUMN_{i}"
        if isinstance(c, int):              # header=False: pandas numbers the columns
            name = f"COLUMN_{c + 1}"
        base, n = name, 2
        while name.upper() in seen:
            name = f"{base}_{n}"
            n += 1
        seen.add(name.upper())
        out.append(name)
    return out


def _to_sqlite_values(df):
    """
    Make every cell something SQLite can store: nested JSON lists/objects
    become JSON text, and timestamps become the ISO text the comparator
    reads back as a TIMESTAMP.
    """
    import json

    import pandas as pd

    df = df.copy()
    for col in df.columns:
        s = df[col]
        if pd.api.types.is_datetime64_any_dtype(s):
            df[col] = s.dt.strftime("%Y-%m-%d %H:%M:%S").where(s.notna(), None)
        elif s.dtype == object:
            df[col] = s.map(lambda v: json.dumps(v, default=str)
                            if isinstance(v, (list, dict)) else v)
    return df


def _unique(name: str, taken: set[str]) -> str:
    candidate, n = name, 2
    while candidate in taken:
        candidate = f"{name}_{n}"
        n += 1
    taken.add(candidate)
    return candidate


def _adapt(value):
    """SQLite has no date or decimal type; hand it the text form the files use."""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value
