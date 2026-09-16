import tkinter
from pathlib import Path

import pytest

from dfjsim_shared_tools import auto_update as auto_update_module
from dfjsim_shared_tools.auto_update import (
    ARCH_STR,
    INSTALLER_NAME_HINT,
    UpdateStatus,
    _check_for_updated_installer,
    _get_installer_version,
    check_for_update,
    describe_installer_dir,
    newest_installer,
)


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


class _DummyRoot:
    """Stands in for the hidden tkinter root, so the tests need no display."""

    def withdraw(self) -> None:
        pass

    def destroy(self) -> None:
        pass


def _installers(directory: Path, *names: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        (directory / name).write_bytes(b"")
    return directory


def test_newest_installer_prefers_the_later_build_of_the_same_version(tmp_path: Path) -> None:
    # Two releases of one version differ by their +build.N tag alone, so that tag decides.
    folder = _installers(
        tmp_path,
        "DemoApp-2.8.0+build.20260101.msi",
        "DemoApp-2.8.0+build.20260801.msi",
        "DemoApp-2.8.0+build.20260501.msi",
    )
    found, found_ver = newest_installer("DemoApp", folder)
    assert found == folder / "DemoApp-2.8.0+build.20260801.msi"
    assert str(found_ver) == "2.8.0+build.20260801"


def test_newest_installer_prefers_a_higher_version_over_a_later_build(tmp_path: Path) -> None:
    folder = _installers(tmp_path, "DemoApp-2.8.0+build.20261231.msi", "DemoApp-2.9.0+build.20260101.msi")
    found, _ = newest_installer("DemoApp", folder)
    assert found == folder / "DemoApp-2.9.0+build.20260101.msi"


def test_newest_installer_reports_an_empty_folder_and_an_unreadable_one_differently(tmp_path: Path) -> None:
    # Nothing to offer is a normal answer; a folder that cannot be read is the caller's problem.
    assert newest_installer("DemoApp", _installers(tmp_path, "notes.txt")) == (None, None)
    with pytest.raises(FileNotFoundError):
        newest_installer("DemoApp", tmp_path / "not-connected")


def test_describe_installer_dir_reports_what_needs_fixing(tmp_path: Path) -> None:
    ok, message = describe_installer_dir("DemoApp", None, "2.8.0+build.20260703")
    assert not ok
    assert "No folder is set" in message

    ok, message = describe_installer_dir("DemoApp", tmp_path / "not-connected", "2.8.0+build.20260703")
    assert not ok
    assert "network share" in message

    ok, message = describe_installer_dir("DemoApp", _installers(tmp_path, "notes.txt"), "2.8.0+build.20260703")
    assert not ok
    assert f"DemoApp-{INSTALLER_NAME_HINT}" in message


def test_describe_installer_dir_separates_up_to_date_from_an_available_update(tmp_path: Path) -> None:
    current = _installers(tmp_path / "current", "DemoApp-2.8.0+build.20260703.msi")
    ok, message = describe_installer_dir("DemoApp", current, "2.8.0+build.20260703")
    assert ok
    assert "up to date" in message

    newer = _installers(tmp_path / "newer", "DemoApp-2.9.0+build.20260801.msi")
    ok, message = describe_installer_dir("DemoApp", newer, "2.8.0+build.20260703")
    assert ok
    assert "newer" in message


def test_describe_installer_dir_refuses_to_compare_an_unparseable_running_version(tmp_path: Path) -> None:
    # An application that could not read its own version must not be told everything is newer.
    folder = _installers(tmp_path, "DemoApp-2.9.0+build.20260801.msi")
    ok, message = describe_installer_dir("DemoApp", folder, "unknown")
    assert not ok
    assert "cannot be compared" in message


def test_check_for_update_without_a_folder_touches_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple] = []
    monkeypatch.setattr(auto_update_module, "newest_installer", lambda *args: calls.append(args))
    assert check_for_update("DemoApp", None, "2.8.0+build.20260703").status is UpdateStatus.OFF
    assert check_for_update("DemoApp", "   ", "2.8.0+build.20260703").status is UpdateStatus.OFF
    assert not calls


def test_check_for_update_asks_before_installing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # No window is not consent: a caller with none yet still gets the dialog, on a hidden parent.
    folder = _installers(tmp_path, "DemoApp-2.9.0+build.20260801.msi")
    asked: list[object] = []
    launched: list[Path] = []

    def accept(window: object) -> bool:
        asked.append(window)
        return True

    monkeypatch.setattr(tkinter, "Tk", _DummyRoot)
    monkeypatch.setattr(auto_update_module, "_show_update_popup", accept)
    monkeypatch.setattr(auto_update_module, "_run_installer", launched.append)

    outcome = check_for_update("DemoApp", folder, "2.8.0+build.20260703")

    assert [type(window) for window in asked] == [_DummyRoot]
    assert launched == [folder / "DemoApp-2.9.0+build.20260801.msi"]
    assert outcome.status is UpdateStatus.ACCEPTED
    assert outcome.installer == launched[0]


def test_check_for_update_says_when_the_offer_was_declined(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = _installers(tmp_path, "DemoApp-2.9.0+build.20260801.msi")
    monkeypatch.setattr(tkinter, "Tk", _DummyRoot)
    monkeypatch.setattr(auto_update_module, "_show_update_popup", lambda window: False)
    monkeypatch.setattr(auto_update_module, "_run_installer", lambda path: pytest.fail("declined, yet launched"))

    outcome = check_for_update("DemoApp", folder, "2.8.0+build.20260703")

    assert outcome.status is UpdateStatus.DECLINED
    assert not outcome.problem
    assert "DemoApp-2.9.0+build.20260801.msi" in outcome.note


def test_check_for_update_survives_an_unreachable_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tkinter, "Tk", _DummyRoot)
    outcome = check_for_update("DemoApp", tmp_path / "not-connected", "2.8.0+build.20260703")
    assert outcome.status is UpdateStatus.UNREACHABLE
    assert outcome.problem
    assert str(tmp_path / "not-connected") in outcome.note


def test_check_for_update_tells_an_empty_folder_from_an_unreachable_one(tmp_path: Path) -> None:
    # The difference an application wants at startup: a share that is not connected is worth a
    # note on the settings form; a folder with no build in it yet is not.
    outcome = check_for_update("DemoApp", _installers(tmp_path, "notes.txt"), "2.8.0+build.20260703")
    assert outcome.status is UpdateStatus.NO_INSTALLER
    assert not outcome.problem
    assert outcome.note  # there is still something to say, for a caller that wants to


def test_check_for_update_reports_up_to_date_with_nothing_to_say(tmp_path: Path) -> None:
    folder = _installers(tmp_path, "DemoApp-2.8.0+build.20260703.msi")
    outcome = check_for_update("DemoApp", folder, "2.8.0+build.20260703")
    assert outcome.status is UpdateStatus.UP_TO_DATE
    assert outcome.note == ""
    assert outcome.installer == folder / "DemoApp-2.8.0+build.20260703.msi"


def test_check_for_update_flags_an_unparseable_running_version_as_a_problem(tmp_path: Path) -> None:
    # A build that lost its version tag would never be offered anything; that is worth pointing out.
    folder = _installers(tmp_path, "DemoApp-2.9.0+build.20260801.msi")
    outcome = check_for_update("DemoApp", folder, "unknown")
    assert outcome.status is UpdateStatus.UNCOMPARABLE
    assert outcome.problem
    assert "unknown" in outcome.note


def test_check_for_update_never_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(*args: object) -> None:
        raise RuntimeError("listing blew up")

    monkeypatch.setattr(auto_update_module, "newest_installer", explode)
    outcome = check_for_update("DemoApp", tmp_path, "2.8.0+build.20260703")
    assert outcome.status is UpdateStatus.ERROR
    assert outcome.problem
    assert "listing blew up" in outcome.note


def test_status_compares_as_its_string(tmp_path: Path) -> None:
    # A consumer may keep a plain string in its own code rather than import the enum.
    assert check_for_update("DemoApp", None, "2.8.0+build.20260703").status == "off"
