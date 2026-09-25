"""Repository behaviour: projects, folders, saved test cases, versioning.

Covers the rules the brief states explicitly — unique test-case ids, no
duplicate names within a folder, configurable folder structure, and edit
history so "the latest saved configuration" is always well defined.
"""
import pytest

from src.repository import DuplicateNameError, NotFoundError, TestCase


def make_tc(folder_id, name="Check something", **kw):
    return TestCase(folder_id=folder_id, test_name=name,
                    test_type=kw.pop("test_type", "SQL_ROWCOUNT"),
                    validation_sql=kw.pop("validation_sql", "SELECT 1 WHERE 1 = 0"), **kw)


class TestProjects:
    def test_create_and_find(self, repo):
        p = repo.create_project("Motor Claims", "desc")
        assert p["PROJECT_NAME"] == "Motor Claims"
        assert repo.find_project_by_name("motor claims")["PROJECT_ID"] == p["PROJECT_ID"]

    def test_duplicate_name_is_rejected(self, repo):
        repo.create_project("Motor Claims")
        with pytest.raises(DuplicateNameError):
            repo.create_project("MOTOR CLAIMS")

    def test_get_or_create_is_idempotent(self, repo):
        a = repo.get_or_create_project("Health")
        b = repo.get_or_create_project("Health")
        assert a["PROJECT_ID"] == b["PROJECT_ID"]
        assert len(repo.list_projects()) == 1

    def test_soft_delete_hides_it(self, repo):
        p = repo.create_project("Temp")
        repo.delete_project(p["PROJECT_ID"])
        assert repo.list_projects() == []
        assert len(repo.list_projects(include_inactive=True)) == 1


class TestFolders:
    def test_create_root_folder(self, repo, project):
        f = repo.create_folder(project["PROJECT_ID"], "Staging")
        assert f["FOLDER_PATH"] == "/Staging"

    def test_nested_folders_build_a_path(self, repo, project):
        parent = repo.create_folder(project["PROJECT_ID"], "Source-to-Target")
        child = repo.create_folder(project["PROJECT_ID"], "Policy",
                                   parent_folder_id=parent["FOLDER_ID"])
        assert child["FOLDER_PATH"] == "/Source-to-Target/Policy"

    def test_duplicate_sibling_name_is_rejected(self, repo, project):
        repo.create_folder(project["PROJECT_ID"], "Staging")
        with pytest.raises(DuplicateNameError):
            repo.create_folder(project["PROJECT_ID"], "staging")

    def test_same_name_under_different_parents_is_fine(self, repo, project):
        a = repo.create_folder(project["PROJECT_ID"], "A")
        b = repo.create_folder(project["PROJECT_ID"], "B")
        repo.create_folder(project["PROJECT_ID"], "Policy", parent_folder_id=a["FOLDER_ID"])
        repo.create_folder(project["PROJECT_ID"], "Policy", parent_folder_id=b["FOLDER_ID"])
        assert len(repo.list_folders(project["PROJECT_ID"])) == 4

    def test_ensure_folder_path_creates_ancestors(self, repo, project):
        leaf = repo.ensure_folder_path(project["PROJECT_ID"], "/A/B/C")
        assert leaf["FOLDER_PATH"] == "/A/B/C"
        assert len(repo.list_folders(project["PROJECT_ID"])) == 3

    def test_ensure_folder_path_is_idempotent(self, repo, project):
        repo.ensure_folder_path(project["PROJECT_ID"], "/A/B")
        repo.ensure_folder_path(project["PROJECT_ID"], "/A/B")
        assert len(repo.list_folders(project["PROJECT_ID"])) == 2

    def test_rename_repaths_descendants(self, repo, project):
        repo.ensure_folder_path(project["PROJECT_ID"], "/Old/Child/Grand")
        old = repo.find_folder_by_path(project["PROJECT_ID"], "/Old")
        repo.rename_folder(old["FOLDER_ID"], "New")
        paths = {f["FOLDER_PATH"] for f in repo.list_folders(project["PROJECT_ID"])}
        assert paths == {"/New", "/New/Child", "/New/Child/Grand"}

    def test_slash_in_name_is_rejected(self, repo, project):
        with pytest.raises(ValueError, match="cannot contain"):
            repo.create_folder(project["PROJECT_ID"], "a/b")

    def test_tree_reports_test_counts(self, repo, project):
        f = repo.create_folder(project["PROJECT_ID"], "Staging")
        repo.create_test_case(make_tc(f["FOLDER_ID"], "one"))
        repo.create_test_case(make_tc(f["FOLDER_ID"], "two"))
        assert repo.folder_tree(project["PROJECT_ID"])[0]["TEST_COUNT"] == 2


class TestTestCases:
    @pytest.fixture
    def folder(self, repo, project):
        return repo.create_folder(project["PROJECT_ID"], "Staging")

    def test_ids_are_unique_and_sequential(self, repo, folder):
        a = repo.create_test_case(make_tc(folder["FOLDER_ID"], "first"))
        b = repo.create_test_case(make_tc(folder["FOLDER_ID"], "second"))
        assert a.test_case_id == "TC-00001"
        assert b.test_case_id == "TC-00002"

    def test_duplicate_name_in_same_folder_is_rejected(self, repo, folder):
        repo.create_test_case(make_tc(folder["FOLDER_ID"], "Row count"))
        with pytest.raises(DuplicateNameError, match="unique within a folder"):
            repo.create_test_case(make_tc(folder["FOLDER_ID"], "ROW COUNT"))

    def test_same_name_in_a_different_folder_is_allowed(self, repo, project, folder):
        other = repo.create_folder(project["PROJECT_ID"], "Curated")
        repo.create_test_case(make_tc(folder["FOLDER_ID"], "Row count"))
        repo.create_test_case(make_tc(other["FOLDER_ID"], "Row count"))
        assert len(repo.list_test_cases(project_id=project["PROJECT_ID"])) == 2

    def test_invalid_test_case_is_rejected(self, repo, folder):
        with pytest.raises(ValueError):
            repo.create_test_case(TestCase(folder_id=folder["FOLDER_ID"], test_name=""))

    def test_edit_bumps_version_and_keeps_history(self, repo, folder):
        tc = repo.create_test_case(make_tc(folder["FOLDER_ID"], "v1 name"))
        tc.validation_sql = "SELECT 2 WHERE 1 = 0"
        tc.test_name = "v2 name"
        updated = repo.update_test_case(tc, change_note="tightened the rule")
        assert updated.version_no == 2
        assert updated.test_name == "v2 name"
        versions = repo.test_case_versions(tc.test_case_id)
        assert [v["VERSION_NO"] for v in versions] == [2, 1, 1]

    def test_get_returns_the_latest_saved_configuration(self, repo, folder):
        tc = repo.create_test_case(make_tc(folder["FOLDER_ID"], "n",
                                           validation_sql="SELECT 'v1' WHERE 1 = 0"))
        tc.validation_sql = "SELECT 'v2' WHERE 1 = 0"
        repo.update_test_case(tc)
        assert "v2" in repo.get_test_case(tc.test_case_id).validation_sql

    def test_restore_an_earlier_version(self, repo, folder):
        tc = repo.create_test_case(make_tc(folder["FOLDER_ID"], "n",
                                           validation_sql="SELECT 'original' WHERE 1 = 0"))
        tc.validation_sql = "SELECT 'broken' WHERE 1 = 0"
        repo.update_test_case(tc)
        restored = repo.restore_version(tc.test_case_id, 1)
        assert "original" in restored.validation_sql
        assert restored.version_no == 3        # restoring is itself a new version

    def test_copy_into_regression_folder(self, repo, project, folder):
        regression = repo.create_folder(project["PROJECT_ID"], "Regression",
                                        folder_type="REGRESSION")
        original = repo.create_test_case(make_tc(folder["FOLDER_ID"], "Policy compare"))
        copy = repo.copy_test_case(original.test_case_id, regression["FOLDER_ID"],
                                   new_name="[Regression] Policy compare")
        assert copy.test_case_id != original.test_case_id
        assert copy.validation_sql == original.validation_sql
        assert copy.version_no == 1

    def test_soft_delete_removes_it_from_listings(self, repo, folder):
        tc = repo.create_test_case(make_tc(folder["FOLDER_ID"], "n"))
        repo.delete_test_case(tc.test_case_id)
        assert repo.list_test_cases(folder_id=folder["FOLDER_ID"]) == []
        assert not repo.get_test_case(tc.test_case_id).is_active

    def test_deleted_name_can_be_reused(self, repo, folder):
        tc = repo.create_test_case(make_tc(folder["FOLDER_ID"], "n"))
        repo.delete_test_case(tc.test_case_id)
        assert repo.create_test_case(make_tc(folder["FOLDER_ID"], "n")).test_case_id != tc.test_case_id

    def test_param_defaults_round_trip_as_json(self, repo, folder):
        tc = make_tc(folder["FOLDER_ID"], "n", validation_sql="SELECT :a WHERE 1 = 0")
        tc.param_defaults = {"a": "2026-01-01", "b": 7}
        saved = repo.create_test_case(tc)
        assert repo.get_test_case(saved.test_case_id).param_defaults == {"a": "2026-01-01", "b": 7}

    def test_missing_test_case_raises(self, repo):
        with pytest.raises(NotFoundError):
            repo.get_test_case("TC-99999")


class TestSeeding:
    """`python -m src.cli seed-catalog` must produce a working project."""

    def test_seed_creates_folders_and_test_cases(self, repo):
        from src.seed import seed_catalog
        summary = seed_catalog(repo=repo)
        assert summary["folders"] == 7
        assert summary["created"] == 18
        assert summary["regression_copies"] == 4

        project = repo.find_project_by_name(summary["project"])
        pid = project["PROJECT_ID"]
        assert len(repo.list_test_cases(project_id=pid)) == 22   # 18 + 4 copies

        regression = repo.find_folder_by_path(pid, "/Regression")
        assert regression["FOLDER_TYPE"] == "REGRESSION"
        assert len(repo.list_test_cases(folder_id=regression["FOLDER_ID"])) == 4

    def test_reseeding_updates_rather_than_duplicating(self, repo):
        from src.seed import seed_catalog
        seed_catalog(repo=repo)
        second = seed_catalog(repo=repo)
        assert second["created"] == 0
        assert second["updated"] == 18
        assert second["regression_copies"] == 0
        pid = repo.find_project_by_name(second["project"])["PROJECT_ID"]
        assert len(repo.list_test_cases(project_id=pid)) == 22

    def test_seeded_test_cases_declare_their_parameters(self, repo):
        from src.seed import seed_catalog
        seed_catalog(repo=repo)
        pid = repo.find_project_by_name("Insurance Policy & Claim")["PROJECT_ID"]
        historical = repo.find_folder_by_path(pid, "/Historical")
        tc = repo.get_test_case(
            repo.list_test_cases(folder_id=historical["FOLDER_ID"])[0]["TEST_CASE_ID"])
        assert set(tc.required_params()) == {"history_start", "history_end"}
        assert tc.param_defaults == {"history_start": "2026-07-01",
                                     "history_end": "2026-12-31"}

    def test_every_seeded_test_case_is_valid(self, repo):
        from src.seed import seed_catalog
        seed_catalog(repo=repo)
        pid = repo.find_project_by_name("Insurance Policy & Claim")["PROJECT_ID"]
        for row in repo.list_test_cases(project_id=pid):
            tc = repo.get_test_case(row["TEST_CASE_ID"])
            assert tc.validate() == [], f"{tc.test_case_id} {tc.test_name}: {tc.validate()}"
