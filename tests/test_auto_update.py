from pathlib import Path

from dfjsim_shared_tools.auto_update import ARCH_STR, _check_for_updated_installer, _get_installer_version


def test_get_installer_version_final_release() -> None:
    path = Path("DemoApp-2.8.0+build.20260703.msi")
    assert _get_installer_version("DemoApp", path) == "2.8.0+build.20260703"


def test_get_installer_version_with_prerelease_marker_and_arch() -> None:
    path = Path(f"DemoApp-2.8.0-beta.1+build.20260703{ARCH_STR}.msi")
    assert _get_installer_version("DemoApp", path) == "2.8.0-beta.1+build.20260703"


def test_get_installer_version_rejects_other_names() -> None:
    assert _get_installer_version("DemoApp", Path("DemoApp-2.8.0.msi")) is None
    assert _get_installer_version("DemoApp", Path("OtherApp-2.8.0+build.1.msi")) is None
    assert _get_installer_version("DemoApp", Path("Copy of DemoApp-2.8.0+build.1.msi")) is None


def test_check_for_updated_installer_offers_final_to_beta_user(tmp_path: Path) -> None:
    final_installer = tmp_path / f"DemoApp-2.8.0+build.20260710{ARCH_STR}.msi"
    final_installer.write_bytes(b"")

    found = _check_for_updated_installer("DemoApp", tmp_path, "2.8.0-beta.1+build.20260703")
    assert found == final_installer


def test_check_for_updated_installer_does_not_offer_beta_to_final_user(tmp_path: Path) -> None:
    beta_installer = tmp_path / f"DemoApp-2.8.0-beta.1+build.20260703{ARCH_STR}.msi"
    beta_installer.write_bytes(b"")

    assert _check_for_updated_installer("DemoApp", tmp_path, "2.8.0+build.20260703") is None


def test_check_for_updated_installer_ignores_unparseable_versions(tmp_path: Path) -> None:
    # Matches the filename pattern but is not a valid PEP 440 version.
    bogus_installer = tmp_path / "DemoApp-2.8.0-bogusmarker+build.1.msi"
    bogus_installer.write_bytes(b"")
    newer_installer = tmp_path / "DemoApp-2.9.0+build.20260801.msi"
    newer_installer.write_bytes(b"")

    found = _check_for_updated_installer("DemoApp", tmp_path, "2.8.0+build.20260703")
    assert found == newer_installer
