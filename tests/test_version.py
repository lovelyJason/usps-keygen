import tomllib
from pathlib import Path

from version import APP_TITLE, APP_VERSION, WINDOWS_DIST_NAME, WORKBENCH_TITLE


def test_version_is_consistent_across_titles_and_project_metadata():
    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())

    assert APP_VERSION == "2.2.4"
    assert project["project"]["version"] == APP_VERSION
    assert f"v{APP_VERSION}" in APP_TITLE
    assert f"v{APP_VERSION}" in WORKBENCH_TITLE
    assert WINDOWS_DIST_NAME == "USPSBatchRegistration-v2.2.4-Windows-x64"
