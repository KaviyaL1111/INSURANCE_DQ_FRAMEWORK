"""
Seeds the repository with the demo project, folder tree and test cases
defined in seed/demo_catalog.yaml.

Idempotent: re-running updates existing test cases in place (bumping their
version) rather than creating duplicates, so you can edit the YAML and
re-seed while developing. Anything you create in the UI is left alone.
"""
from __future__ import annotations

import os

import yaml

from src.config import DEFAULT_CONNECTION, DEFAULT_USER, ROOT_DIR
from src.repository import Repository, TestCase

SEED_PATH = os.path.join(ROOT_DIR, "seed", "demo_catalog.yaml")


def load_seed(path: str = SEED_PATH) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def _test_case_from_seed(entry: dict, project_id: str, folder_id: str) -> TestCase:
    return TestCase(
        project_id=project_id,
        folder_id=folder_id,
        test_name=entry["name"],
        description=(entry.get("description") or "").strip(),
        test_type=entry.get("test_type", "SQL_ROWCOUNT"),
        validation_scope=entry.get("scope", "GENERIC"),
        entity=entry.get("entity", ""),
        layer=entry.get("layer", ""),
        severity=entry.get("severity", "Medium"),
        source_connection=entry.get("source_connection", DEFAULT_CONNECTION),
        target_connection=entry.get("target_connection", DEFAULT_CONNECTION),
        source_sql=(entry.get("source_sql") or "").strip(),
        target_sql=(entry.get("target_sql") or "").strip(),
        validation_sql=(entry.get("validation_sql") or "").strip(),
        key_columns=entry.get("key_columns", ""),
        compare_columns=entry.get("compare_columns", ""),
        param_defaults=entry.get("param_defaults") or {},
        tolerance=float(entry.get("tolerance", 0) or 0),
        is_regression=bool(entry.get("is_regression", True)),
    )


def seed_catalog(repo: Repository | None = None, path: str = SEED_PATH,
                 created_by: str = "", update_existing: bool = True) -> dict:
    own = repo is None
    repo = repo or Repository()
    created_by = created_by or DEFAULT_USER
    try:
        spec = load_seed(path)
        project = repo.get_or_create_project(
            spec["project"]["name"],
            (spec["project"].get("description") or "").strip(),
            created_by=created_by,
        )
        pid = project["PROJECT_ID"]

        folders = {}
        for f in spec.get("folders", []):
            folder = repo.ensure_folder_path(
                pid, f["path"], folder_type=f.get("type", "STANDARD"), created_by=created_by)
            folders[f["path"]] = folder

        summary = {"project": project["PROJECT_NAME"], "project_id": pid,
                   "folders": len(folders), "created": 0, "updated": 0, "unchanged": 0}
        by_name = {}

        for entry in spec.get("test_cases", []):
            folder = folders.get(entry["folder"]) or repo.ensure_folder_path(
                pid, entry["folder"], created_by=created_by)
            existing = repo.find_test_case_by_name(folder["FOLDER_ID"], entry["name"])
            tc = _test_case_from_seed(entry, pid, folder["FOLDER_ID"])
            if existing is None:
                saved = repo.create_test_case(tc, created_by=created_by)
                summary["created"] += 1
            elif update_existing:
                tc.test_case_id = existing["TEST_CASE_ID"]
                saved = repo.update_test_case(tc, updated_by=created_by,
                                              change_note="Re-seeded from demo_catalog.yaml")
                summary["updated"] += 1
            else:
                saved = repo.get_test_case(existing["TEST_CASE_ID"])
                summary["unchanged"] += 1
            by_name[entry["name"]] = saved

        # Saved regression scripts: independent copies living in /Regression.
        regression_folder = folders.get("/Regression") or repo.ensure_folder_path(
            pid, "/Regression", folder_type="REGRESSION", created_by=created_by)
        summary["regression_copies"] = 0
        for name in spec.get("regression_copies", []):
            source = by_name.get(name)
            if source is None:
                continue
            copy_name = f"[Regression] {name}"
            if repo.find_test_case_by_name(regression_folder["FOLDER_ID"], copy_name):
                continue
            repo.copy_test_case(source.test_case_id, regression_folder["FOLDER_ID"],
                                new_name=copy_name, created_by=created_by)
            summary["regression_copies"] += 1

        return summary
    finally:
        if own:
            repo.close()
