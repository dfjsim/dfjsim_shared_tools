from pathlib import Path

import pytest

from dfjsim_shared_tools.build_nuitka_wix_installer import _resolve_msi_version, load_build_config


def test_load_build_config_reads_expected_values(tmp_path: Path, monkeypatch) -> None:
    custom_setup = tmp_path / "custom_setup.wxs"
    custom_ui = tmp_path / "custom_ui.wxs"
    custom_setup.write_text("<Wix />", encoding="utf-8")
    custom_ui.write_text("<Wix />", encoding="utf-8")

    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "demo_app"
version = "1.2.3+build.4"
authors = [{ name = "Demo Author" }]

[tool.msi]
upgrade_code = "12345678-1234-1234-1234-1234567890AB"
allow_downgrades = true
remove_existing_products = false

[tool.wix-build]
copy_files = ["config.toml", "configs_default"]
setup_wxs = "custom_setup.wxs"
ui_wxs = "custom_ui.wxs"

[tool.wix-nuitka]
entry_point = "main.py"
plugins = ["pyside6"]
qt_plugins = ["platforms"]
include_modules = ["demo.module"]
windows_console_mode = "force"
ui_file = "gui/main.ui"
ui_py_file = "gui/main_ui.py"
ui_compile_pairs = [
    { ui = "gui/extra.ui", py = "gui/extra_ui.py" },
]
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    config = load_build_config()

    assert config.app_name == "demo_app"
    assert config.app_version_short == "1.2.3"
    assert config.app_manufacturer == "Demo Author"
    assert config.allow_downgrades is True
    assert config.remove_existing_products is False
    assert config.copy_files == ("config.toml", "configs_default")
    assert config.entry_point == "main.py"
    assert config.nuitka_plugins == ("pyside6",)
    assert config.nuitka_qt_plugins == ("platforms",)
    assert config.nuitka_include_modules == ("demo.module",)
    assert config.nuitka_windows_console_mode == "force"
    assert config.ui_compile_pairs == (
        ("gui/main.ui", "gui/main_ui.py"),
        ("gui/extra.ui", "gui/extra_ui.py"),
    )
    assert config.wix_setup_template == custom_setup.resolve()
    assert config.wix_ui_template == custom_ui.resolve()
    assert config.msi_output.name.startswith("demo_app-1.2.3+build.4")


def test_resolve_msi_version_prefers_explicit_tool_msi_version() -> None:
    assert _resolve_msi_version("2.8.0-beta.1+build.20260703", {"version": "2.8.0.20260703"}) == "2.8.0.20260703"


def test_resolve_msi_version_falls_back_to_project_version() -> None:
    assert _resolve_msi_version("1.2.3+build.4", {}) == "1.2.3"
    assert _resolve_msi_version("1.2.3", {}) == "1.2.3"


def test_resolve_msi_version_rejects_prerelease_without_explicit_version() -> None:
    with pytest.raises(ValueError, match=r"\[tool\.msi\]\.version"):
        _resolve_msi_version("2.8.0-beta.1+build.20260703", {})


def test_resolve_msi_version_rejects_non_numeric_explicit_version() -> None:
    with pytest.raises(ValueError, match="not a valid MSI ProductVersion"):
        _resolve_msi_version("2.8.0+build.1", {"version": "2.8.0-beta"})


def test_load_build_config_uses_tool_msi_version(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "demo_app"
version = "2.8.0-beta.1+build.20260703"

[tool.msi]
upgrade_code = "12345678-1234-1234-1234-1234567890AB"
version = "2.8.0.20260703"
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    config = load_build_config()

    assert config.app_version == "2.8.0-beta.1+build.20260703"
    assert config.app_version_short == "2.8.0.20260703"
    assert config.msi_output.name.startswith("demo_app-2.8.0-beta.1+build.20260703")
