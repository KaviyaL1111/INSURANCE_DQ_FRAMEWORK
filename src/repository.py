"""
Validation repository.

Everything the application saves lives here: projects, the folder tree,
test-case definitions, their edit history, execution history and failure
detail. All of it is stored in the control tables created by
``sql/00_metadata_repository_ddl.sql``.

Rules the brief requires, enforced in this module (Snowflake does not
enforce UNIQUE constraints, so they are enforced on write):
  * a project name is unique across the repository
  * a test-case name is unique within its folder
  * a folder name is unique among its siblings
  * every saved test case gets a unique, human-readable TEST_CASE_ID
  * every edit archives the previous version, so "the latest saved
    configuration" is always well defined
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone

from src.config import DEFAULT_USER, SQL_DIR
from src.connectors import get_connector

TEST_TYPES = [
    "SOURCE_TARGET_COMPARE",  # row-by-row source vs target, mismatch detail per column
    "SQL_ROWCOUNT",           # a rule query: zero rows returned = PASS
    "ROW_COUNT_MATCH",        # source count vs target count
    "STATUS_COLUMN",          # query returns a STATUS column of PASS/FAIL
    "INFORMATIONAL",          # always PASS; used for reports and watermark views
]

VALIDATION_SCOPES = ["SOURCE_TO_TARGET", "HISTORICAL", "INCREMENTAL", "GENERIC"]
SEVERITIES = ["Critical", "High", "Medium", "Low"]


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _uid() -> str:
    return uuid.uuid4().hex


class DuplicateNameError(ValueError):
    """Raised when a name would collide within its scope."""


class NotFoundError(LookupError):
    """Raised when a project / folder / test case does not exist."""


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------
@dataclass
class TestCase:
    __test__ = False  # this is a domain object, not a pytest test class

    test_case_id: str = ""
    project_id: str = ""
    folder_id: str = ""
    test_name: str = ""
    description: str = ""
    test_type: str = "SQL_ROWCOUNT"
    validation_scope: str = "GENERIC"
    entity: str = ""
    layer: str = ""
    severity: str = "Medium"
    source_connection: str = "snowflake"
    target_connection: str = "snowflake"
    source_sql: str = ""
    target_sql: str = ""
    validation_sql: str = ""
    key_columns: str = ""
    compare_columns: str = ""
    param_defaults: dict = field(default_factory=dict)
    tolerance: float = 0.0
    is_regression: bool = True
    is_active: bool = True
    version_no: int = 1
    created_by: str = ""
    created_ts: datetime | None = None
    updated_by: str = ""
    updated_ts: datetime | None = None

    # convenience -----------------------------------------------------------
    @property
    def key_column_list(self) -> list[str]:
        return [c.strip() for c in (self.key_columns or "").split(",") if c.strip()]

    @property
    def compare_column_list(self) -> list[str]:
        return [c.strip() for c in (self.compare_columns or "").split(",") if c.strip()]

    def required_params(self) -> list[str]:
        from src.connectors.base import extract_params
        seen, out = set(), []
        for sql in (self.validation_sql, self.source_sql, self.target_sql):
            for p in extract_params(sql or ""):
                if p not in seen:
                    seen.add(p)
                    out.append(p)
        return out

    def validate(self) -> list[str]:
        """Return a list of human-readable problems; empty means saveable."""
        problems = []
        if not self.test_name.strip():
            problems.append("Test name is required.")
        if self.test_type not in TEST_TYPES:
            problems.append(f"Test type must be one of: {', '.join(TEST_TYPES)}")
        if self.test_type in ("SOURCE_TARGET_COMPARE", "ROW_COUNT_MATCH"):
            if not self.source_sql.strip():
                problems.append("Source SQL is required for this test type.")
            if not self.target_sql.strip():
                problems.append("Target SQL is required for this test type.")
            if self.test_type == "SOURCE_TARGET_COMPARE" and not self.key_column_list:
                problems.append("Key column(s) are required to match source rows to target rows.")
        elif not self.validation_sql.strip():
            problems.append("Validation SQL is required for this test type.")
        return problems

    def to_row(self) -> dict:
        d = asdict(self)
        d["param_defaults"] = json.dumps(self.param_defaults or {})
        return d

    @classmethod
    def from_row(cls, row: dict) -> "TestCase":
        low = {k.lower(): v for k, v in row.items()}
        kwargs = {}
        for f in fields(cls):
            if f.name not in low:
                continue
            v = low[f.name]
            if f.name == "param_defaults":
                v = json.loads(v) if isinstance(v, str) and v.strip() else (v or {})
            elif f.name == "tolerance":
                v = float(v or 0)
            elif f.name == "version_no":
                v = int(v or 1)
            elif f.name in ("is_regression", "is_active"):
                v = bool(v)
            elif v is None and f.name not in ("created_ts", "updated_ts"):
                v = ""
            kwargs[f.name] = v
        return cls(**kwargs)


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------
class Repository:
    """
    Read/write access to the control tables.

    The repository always talks to ONE database (the default connection) —
    test cases it stores may point at any other connection for their own
    source and target.
    """

    def __init__(self, connector=None, connection_name: str | None = None):
        self._own = connector is None
        self.db = connector or get_connector(connection_name)

    def close(self):
        if self._own:
            self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    # -- schema -------------------------------------------------------------
    def init_schema(self) -> None:
        import os
        with open(os.path.join(SQL_DIR, "00_metadata_repository_ddl.sql")) as f:
            self.db.run_script(f.read())

    # ================================================================ projects
    def create_project(self, name: str, description: str = "", created_by: str = "") -> dict:
        name = name.strip()
        if not name:
            raise ValueError("Project name cannot be empty.")
        if self.find_project_by_name(name):
            raise DuplicateNameError(f"A project named '{name}' already exists.")
        pid = _uid()
        self.db.execute(
            """INSERT INTO DQ_PROJECT (PROJECT_ID, PROJECT_NAME, DESCRIPTION, IS_ACTIVE,
                                       CREATED_BY, CREATED_TS, UPDATED_TS)
               VALUES (:pid, :name, :descr, TRUE, :by, :ts, :ts)""",
            {"pid": pid, "name": name, "descr": description,
             "by": created_by or DEFAULT_USER, "ts": _now()},
        )
        self.db.commit()
        return self.get_project(pid)

    def list_projects(self, include_inactive: bool = False) -> list[dict]:
        sql = """SELECT PROJECT_ID, PROJECT_NAME, DESCRIPTION, IS_ACTIVE, CREATED_BY, CREATED_TS
                 FROM DQ_PROJECT"""
        if not include_inactive:
            sql += " WHERE IS_ACTIVE = TRUE"
        sql += " ORDER BY PROJECT_NAME"
        return self.db.query(sql).to_dicts()

    def get_project(self, project_id: str) -> dict:
        rows = self.db.query(
            "SELECT * FROM DQ_PROJECT WHERE PROJECT_ID = :pid", {"pid": project_id}
        ).to_dicts()
        if not rows:
            raise NotFoundError(f"No project with id {project_id}")
        return rows[0]

    def find_project_by_name(self, name: str) -> dict | None:
        rows = self.db.query(
            "SELECT * FROM DQ_PROJECT WHERE UPPER(PROJECT_NAME) = UPPER(:name)", {"name": name.strip()}
        ).to_dicts()
        return rows[0] if rows else None

    def get_or_create_project(self, name: str, description: str = "", created_by: str = "") -> dict:
        return self.find_project_by_name(name) or self.create_project(name, description, created_by)

    def rename_project(self, project_id: str, new_name: str) -> dict:
        new_name = new_name.strip()
        existing = self.find_project_by_name(new_name)
        if existing and existing["PROJECT_ID"] != project_id:
            raise DuplicateNameError(f"A project named '{new_name}' already exists.")
        self.db.execute(
            "UPDATE DQ_PROJECT SET PROJECT_NAME = :name, UPDATED_TS = :ts WHERE PROJECT_ID = :pid",
            {"name": new_name, "ts": _now(), "pid": project_id},
        )
        self.db.commit()
        return self.get_project(project_id)

    def delete_project(self, project_id: str, hard: bool = False) -> None:
        if hard:
            for stmt in (
                "DELETE FROM DQ_TEST_CASE WHERE PROJECT_ID = :pid",
                "DELETE FROM DQ_FOLDER WHERE PROJECT_ID = :pid",
                "DELETE FROM DQ_PROJECT WHERE PROJECT_ID = :pid",
            ):
                self.db.execute(stmt, {"pid": project_id})
        else:
            self.db.execute(
                "UPDATE DQ_PROJECT SET IS_ACTIVE = FALSE, UPDATED_TS = :ts WHERE PROJECT_ID = :pid",
                {"ts": _now(), "pid": project_id},
            )
        self.db.commit()

    # ================================================================= folders
    def create_folder(self, project_id: str, name: str, parent_folder_id: str | None = None,
                      folder_type: str = "STANDARD", description: str = "",
                      created_by: str = "") -> dict:
        name = name.strip()
        if not name:
            raise ValueError("Folder name cannot be empty.")
        if "/" in name:
            raise ValueError("Folder name cannot contain '/'. Create a sub-folder instead.")

        siblings = self.list_folders(project_id, parent_folder_id=parent_folder_id)
        if any(f["FOLDER_NAME"].upper() == name.upper() for f in siblings):
            where = "this project" if parent_folder_id is None else "that parent folder"
            raise DuplicateNameError(f"A folder named '{name}' already exists in {where}.")

        if parent_folder_id:
            parent = self.get_folder(parent_folder_id)
            path = f"{parent['FOLDER_PATH'].rstrip('/')}/{name}"
        else:
            path = f"/{name}"

        fid = _uid()
        self.db.execute(
            """INSERT INTO DQ_FOLDER (FOLDER_ID, PROJECT_ID, PARENT_FOLDER_ID, FOLDER_NAME,
                                      FOLDER_PATH, FOLDER_TYPE, DESCRIPTION, IS_ACTIVE, CREATED_BY, CREATED_TS)
               VALUES (:fid, :pid, :parent, :name, :path, :ftype, :descr, TRUE, :by, :ts)""",
            {"fid": fid, "pid": project_id, "parent": parent_folder_id, "name": name,
             "path": path, "ftype": folder_type, "descr": description,
             "by": created_by or DEFAULT_USER, "ts": _now()},
        )
        self.db.commit()
        return self.get_folder(fid)

    def list_folders(self, project_id: str, parent_folder_id: str | None = "__ANY__",
                     include_inactive: bool = False) -> list[dict]:
        sql = """SELECT FOLDER_ID, PROJECT_ID, PARENT_FOLDER_ID, FOLDER_NAME, FOLDER_PATH,
                        FOLDER_TYPE, DESCRIPTION, IS_ACTIVE, CREATED_TS
                 FROM DQ_FOLDER WHERE PROJECT_ID = :pid"""
        params = {"pid": project_id}
        if not include_inactive:
            sql += " AND IS_ACTIVE = TRUE"
        if parent_folder_id != "__ANY__":
            if parent_folder_id is None:
                sql += " AND PARENT_FOLDER_ID IS NULL"
            else:
                sql += " AND PARENT_FOLDER_ID = :parent"
                params["parent"] = parent_folder_id
        sql += " ORDER BY FOLDER_PATH"
        return self.db.query(sql, params).to_dicts()

    def get_folder(self, folder_id: str) -> dict:
        rows = self.db.query(
            "SELECT * FROM DQ_FOLDER WHERE FOLDER_ID = :fid", {"fid": folder_id}
        ).to_dicts()
        if not rows:
            raise NotFoundError(f"No folder with id {folder_id}")
        return rows[0]

    def find_folder_by_path(self, project_id: str, path: str) -> dict | None:
        rows = self.db.query(
            "SELECT * FROM DQ_FOLDER WHERE PROJECT_ID = :pid AND UPPER(FOLDER_PATH) = UPPER(:path)",
            {"pid": project_id, "path": path if path.startswith("/") else "/" + path},
        ).to_dicts()
        return rows[0] if rows else None

    def ensure_folder_path(self, project_id: str, path: str, folder_type: str = "STANDARD",
                           created_by: str = "") -> dict:
        """Create '/A/B/C' and any missing ancestors; return the leaf folder."""
        parts = [p for p in path.strip("/").split("/") if p]
        parent_id, current, folder = None, "", None
        for i, part in enumerate(parts):
            current = f"{current}/{part}"
            folder = self.find_folder_by_path(project_id, current)
            if folder is None:
                is_leaf = i == len(parts) - 1
                folder = self.create_folder(
                    project_id, part, parent_folder_id=parent_id,
                    folder_type=folder_type if is_leaf else "STANDARD",
                    created_by=created_by,
                )
            parent_id = folder["FOLDER_ID"]
        if folder is None:
            raise ValueError("Folder path cannot be empty.")
        return folder

    def rename_folder(self, folder_id: str, new_name: str) -> dict:
        new_name = new_name.strip()
        if "/" in new_name:
            raise ValueError("Folder name cannot contain '/'.")
        folder = self.get_folder(folder_id)
        siblings = self.list_folders(folder["PROJECT_ID"], parent_folder_id=folder["PARENT_FOLDER_ID"])
        if any(f["FOLDER_NAME"].upper() == new_name.upper() and f["FOLDER_ID"] != folder_id
               for f in siblings):
            raise DuplicateNameError(f"A folder named '{new_name}' already exists here.")

        old_path = folder["FOLDER_PATH"]
        parent_path = old_path.rsplit("/", 1)[0]
        new_path = f"{parent_path}/{new_name}"
        self.db.execute(
            "UPDATE DQ_FOLDER SET FOLDER_NAME = :name, FOLDER_PATH = :path WHERE FOLDER_ID = :fid",
            {"name": new_name, "path": new_path, "fid": folder_id},
        )
        # Re-path descendants.
        self.db.execute(
            """UPDATE DQ_FOLDER
               SET FOLDER_PATH = :new_path || SUBSTR(FOLDER_PATH, :cut)
               WHERE PROJECT_ID = :pid AND FOLDER_PATH LIKE :like_old""",
            {"new_path": new_path, "cut": len(old_path) + 1,
             "pid": folder["PROJECT_ID"], "like_old": old_path + "/%"},
        )
        self.db.commit()
        return self.get_folder(folder_id)

    def delete_folder(self, folder_id: str, hard: bool = False) -> None:
        folder = self.get_folder(folder_id)
        descendants = self.db.query(
            "SELECT FOLDER_ID FROM DQ_FOLDER WHERE PROJECT_ID = :pid AND FOLDER_PATH LIKE :like",
            {"pid": folder["PROJECT_ID"], "like": folder["FOLDER_PATH"] + "%"},
        ).to_dicts()
        ids = [d["FOLDER_ID"] for d in descendants] or [folder_id]
        for fid in ids:
            if hard:
                self.db.execute("DELETE FROM DQ_TEST_CASE WHERE FOLDER_ID = :fid", {"fid": fid})
                self.db.execute("DELETE FROM DQ_FOLDER WHERE FOLDER_ID = :fid", {"fid": fid})
            else:
                self.db.execute("UPDATE DQ_TEST_CASE SET IS_ACTIVE = FALSE WHERE FOLDER_ID = :fid",
                                {"fid": fid})
                self.db.execute("UPDATE DQ_FOLDER SET IS_ACTIVE = FALSE WHERE FOLDER_ID = :fid",
                                {"fid": fid})
        self.db.commit()

    def folder_tree(self, project_id: str) -> list[dict]:
        """Folders with their test-case counts, ordered for tree rendering."""
        return self.db.query(
            """SELECT F.FOLDER_ID, F.PARENT_FOLDER_ID, F.FOLDER_NAME, F.FOLDER_PATH, F.FOLDER_TYPE,
                      COUNT(T.TEST_CASE_ID) AS TEST_COUNT
               FROM DQ_FOLDER F
               LEFT JOIN DQ_TEST_CASE T
                      ON T.FOLDER_ID = F.FOLDER_ID AND T.IS_ACTIVE = TRUE
               WHERE F.PROJECT_ID = :pid AND F.IS_ACTIVE = TRUE
               GROUP BY F.FOLDER_ID, F.PARENT_FOLDER_ID, F.FOLDER_NAME, F.FOLDER_PATH, F.FOLDER_TYPE
               ORDER BY F.FOLDER_PATH""",
            {"pid": project_id},
        ).to_dicts()

    # ============================================================== test cases
    def _next_test_case_id(self) -> str:
        """
        Next free TC-00001 style id.

        The maximum is computed in Python rather than with TRY_TO_NUMBER so the
        repository is not tied to Snowflake's dialect. One narrow column over a
        few thousand rows is negligible, and create_test_case retries on the
        primary key if two clients race for the same number.
        """
        rows = self.db.query(
            "SELECT TEST_CASE_ID FROM DQ_TEST_CASE WHERE TEST_CASE_ID LIKE 'TC-%'"
        ).rows
        highest = 0
        for (tcid,) in rows:
            suffix = str(tcid)[3:]
            if suffix.isdigit():
                highest = max(highest, int(suffix))
        return f"TC-{highest + 1:05d}"

    def find_test_case_by_name(self, folder_id: str, test_name: str) -> dict | None:
        rows = self.db.query(
            """SELECT * FROM DQ_TEST_CASE
               WHERE FOLDER_ID = :fid AND UPPER(TEST_NAME) = UPPER(:name) AND IS_ACTIVE = TRUE""",
            {"fid": folder_id, "name": test_name.strip()},
        ).to_dicts()
        return rows[0] if rows else None

    def create_test_case(self, tc: TestCase, created_by: str = "") -> TestCase:
        problems = tc.validate()
        if problems:
            raise ValueError(" ".join(problems))
        if not tc.folder_id:
            raise ValueError("A test case must be saved into a folder.")

        folder = self.get_folder(tc.folder_id)
        tc.project_id = tc.project_id or folder["PROJECT_ID"]

        # Requirement: prevent duplicate test-case names within the same folder.
        if self.find_test_case_by_name(tc.folder_id, tc.test_name):
            raise DuplicateNameError(
                f"A test case named '{tc.test_name}' already exists in folder "
                f"{folder['FOLDER_PATH']}. Test-case names must be unique within a folder."
            )

        tc.created_by = created_by or DEFAULT_USER
        tc.updated_by = tc.created_by
        tc.created_ts = tc.updated_ts = _now()
        tc.version_no = 1

        # Retry guards against two clients picking the same generated id.
        for attempt in range(5):
            tc.test_case_id = self._next_test_case_id()
            try:
                self._insert_test_case(tc)
                break
            except Exception as exc:
                if attempt == 4 or not _is_duplicate_key(exc):
                    raise
        self._archive_version(tc, "Created")
        self.db.commit()
        return self.get_test_case(tc.test_case_id)

    def _insert_test_case(self, tc: TestCase) -> None:
        r = tc.to_row()
        self.db.execute(
            """INSERT INTO DQ_TEST_CASE (
                   TEST_CASE_ID, PROJECT_ID, FOLDER_ID, TEST_NAME, DESCRIPTION, TEST_TYPE,
                   VALIDATION_SCOPE, ENTITY, LAYER, SEVERITY, SOURCE_CONNECTION, TARGET_CONNECTION,
                   SOURCE_SQL, TARGET_SQL, VALIDATION_SQL, KEY_COLUMNS, COMPARE_COLUMNS,
                   PARAM_DEFAULTS, TOLERANCE, IS_REGRESSION, IS_ACTIVE, VERSION_NO,
                   CREATED_BY, CREATED_TS, UPDATED_BY, UPDATED_TS)
               VALUES (:test_case_id, :project_id, :folder_id, :test_name, :description, :test_type,
                       :validation_scope, :entity, :layer, :severity, :source_connection, :target_connection,
                       :source_sql, :target_sql, :validation_sql, :key_columns, :compare_columns,
                       :param_defaults, :tolerance, :is_regression, :is_active, :version_no,
                       :created_by, :created_ts, :updated_by, :updated_ts)""",
            r,
        )

    def update_test_case(self, tc: TestCase, updated_by: str = "",
                         change_note: str = "Edited") -> TestCase:
        problems = tc.validate()
        if problems:
            raise ValueError(" ".join(problems))
        current = self.get_test_case(tc.test_case_id)

        # Name must stay unique within the (possibly new) folder.
        clash = self.find_test_case_by_name(tc.folder_id, tc.test_name)
        if clash and clash["TEST_CASE_ID"] != tc.test_case_id:
            raise DuplicateNameError(
                f"A test case named '{tc.test_name}' already exists in that folder."
            )

        # Archive the state we are replacing, then bump the version.
        self._archive_version(current, f"Superseded by v{current.version_no + 1}")
        tc.version_no = current.version_no + 1
        tc.created_by = current.created_by
        tc.created_ts = current.created_ts
        tc.updated_by = updated_by or DEFAULT_USER
        tc.updated_ts = _now()

        r = tc.to_row()
        self.db.execute(
            """UPDATE DQ_TEST_CASE SET
                   PROJECT_ID = :project_id, FOLDER_ID = :folder_id, TEST_NAME = :test_name,
                   DESCRIPTION = :description, TEST_TYPE = :test_type,
                   VALIDATION_SCOPE = :validation_scope, ENTITY = :entity, LAYER = :layer,
                   SEVERITY = :severity, SOURCE_CONNECTION = :source_connection,
                   TARGET_CONNECTION = :target_connection, SOURCE_SQL = :source_sql,
                   TARGET_SQL = :target_sql, VALIDATION_SQL = :validation_sql,
                   KEY_COLUMNS = :key_columns, COMPARE_COLUMNS = :compare_columns,
                   PARAM_DEFAULTS = :param_defaults, TOLERANCE = :tolerance,
                   IS_REGRESSION = :is_regression, IS_ACTIVE = :is_active,
                   VERSION_NO = :version_no, UPDATED_BY = :updated_by, UPDATED_TS = :updated_ts
               WHERE TEST_CASE_ID = :test_case_id""",
            r,
        )
        self._archive_version(tc, change_note)
        self.db.commit()
        return self.get_test_case(tc.test_case_id)

    def _archive_version(self, tc: TestCase, note: str) -> None:
        snapshot = tc.to_row()
        for k in ("created_ts", "updated_ts"):
            if isinstance(snapshot.get(k), datetime):
                snapshot[k] = snapshot[k].isoformat()
        self.db.execute(
            """INSERT INTO DQ_TEST_CASE_VERSION
                   (VERSION_ID, TEST_CASE_ID, VERSION_NO, SNAPSHOT_JSON, CHANGE_NOTE, CHANGED_BY, CHANGED_TS)
               VALUES (:vid, :tcid, :vno, :snap, :note, :by, :ts)""",
            {"vid": _uid(), "tcid": tc.test_case_id, "vno": tc.version_no,
             "snap": json.dumps(snapshot, default=str), "note": note,
             "by": tc.updated_by or DEFAULT_USER, "ts": _now()},
        )

    def get_test_case(self, test_case_id: str) -> TestCase:
        """Load the LATEST saved configuration of a test case."""
        rows = self.db.query(
            "SELECT * FROM DQ_TEST_CASE WHERE TEST_CASE_ID = :tcid", {"tcid": test_case_id}
        ).to_dicts()
        if not rows:
            raise NotFoundError(f"No test case with id {test_case_id}")
        return TestCase.from_row(rows[0])

    def get_test_cases(self, test_case_ids: list[str]) -> list[TestCase]:
        return [self.get_test_case(t) for t in test_case_ids]

    def list_test_cases(self, folder_id: str | None = None, project_id: str | None = None,
                        include_inactive: bool = False, regression_only: bool = False) -> list[dict]:
        sql = """SELECT T.TEST_CASE_ID, T.TEST_NAME, T.DESCRIPTION, T.TEST_TYPE, T.VALIDATION_SCOPE,
                        T.ENTITY, T.LAYER, T.SEVERITY, T.SOURCE_CONNECTION, T.TARGET_CONNECTION,
                        T.IS_REGRESSION, T.IS_ACTIVE, T.VERSION_NO, T.UPDATED_TS,
                        F.FOLDER_PATH, F.FOLDER_ID
                 FROM DQ_TEST_CASE T JOIN DQ_FOLDER F ON F.FOLDER_ID = T.FOLDER_ID
                 WHERE 1 = 1"""
        params: dict = {}
        if folder_id:
            sql += " AND T.FOLDER_ID = :fid"
            params["fid"] = folder_id
        if project_id:
            sql += " AND T.PROJECT_ID = :pid"
            params["pid"] = project_id
        if not include_inactive:
            sql += " AND T.IS_ACTIVE = TRUE"
        if regression_only:
            sql += " AND T.IS_REGRESSION = TRUE"
        sql += " ORDER BY F.FOLDER_PATH, T.TEST_CASE_ID"
        return self.db.query(sql, params).to_dicts()

    def delete_test_case(self, test_case_id: str, hard: bool = False) -> None:
        if hard:
            self.db.execute("DELETE FROM DQ_TEST_CASE WHERE TEST_CASE_ID = :tcid", {"tcid": test_case_id})
        else:
            self.db.execute(
                "UPDATE DQ_TEST_CASE SET IS_ACTIVE = FALSE, UPDATED_TS = :ts WHERE TEST_CASE_ID = :tcid",
                {"ts": _now(), "tcid": test_case_id},
            )
        self.db.commit()

    def copy_test_case(self, test_case_id: str, target_folder_id: str,
                       new_name: str | None = None, created_by: str = "") -> TestCase:
        """Copy a saved test case into another folder — e.g. into /Regression."""
        src = self.get_test_case(test_case_id)
        clone = TestCase(**{**asdict(src), "test_case_id": "", "folder_id": target_folder_id,
                            "test_name": new_name or src.test_name, "version_no": 1})
        clone.param_defaults = dict(src.param_defaults or {})
        return self.create_test_case(clone, created_by=created_by)

    def test_case_versions(self, test_case_id: str) -> list[dict]:
        return self.db.query(
            """SELECT VERSION_NO, CHANGE_NOTE, CHANGED_BY, CHANGED_TS
               FROM DQ_TEST_CASE_VERSION WHERE TEST_CASE_ID = :tcid
               ORDER BY VERSION_NO DESC, CHANGED_TS DESC""",
            {"tcid": test_case_id},
        ).to_dicts()

    def restore_version(self, test_case_id: str, version_no: int, restored_by: str = "") -> TestCase:
        rows = self.db.query(
            """SELECT SNAPSHOT_JSON FROM DQ_TEST_CASE_VERSION
               WHERE TEST_CASE_ID = :tcid AND VERSION_NO = :vno
               ORDER BY CHANGED_TS DESC LIMIT 1""",
            {"tcid": test_case_id, "vno": version_no},
        ).to_dicts()
        if not rows:
            raise NotFoundError(f"Test case {test_case_id} has no version {version_no}")
        snap = json.loads(rows[0]["SNAPSHOT_JSON"])
        snap["param_defaults"] = json.loads(snap["param_defaults"]) if isinstance(
            snap.get("param_defaults"), str) else (snap.get("param_defaults") or {})
        snap.pop("created_ts", None)
        snap.pop("updated_ts", None)
        restored = TestCase(**{k: v for k, v in snap.items()
                               if k in {f.name for f in fields(TestCase)}})
        return self.update_test_case(restored, updated_by=restored_by,
                                     change_note=f"Restored from v{version_no}")


def _is_duplicate_key(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "duplicate" in msg or "unique" in msg or "primary key" in msg
