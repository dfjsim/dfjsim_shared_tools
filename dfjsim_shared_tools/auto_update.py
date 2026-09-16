"""Utilities for optional MSI/EXE self-update checks.

An application publishes its installers to a folder — a shared drive, typically — and each install
looks there at startup for a newer one. There is no update server and nothing is sent anywhere:
the whole protocol is a directory listing plus a filename comparison.

Two entry points, depending on how much the caller wants to own:

* :func:`check_for_update` is the one an application normally wants. It always asks before
  installing, makes its own hidden tkinter parent window when the caller has none yet, treats an
  unset folder as "the feature is off", and never raises — an update check must not be able to
  stop an application from starting. It returns an :class:`UpdateCheck` saying what happened, so
  an application can tell "the share is not connected" (worth a note on the settings form) from
  "nothing there yet" (the normal state of a folder that was just set up) without either of them
  becoming a dialog.
* :func:`auto_update` is the primitive underneath it. It raises on a bad folder, and installs
  **without asking** when it is given no window, which is why it is not the recommended default.

:func:`describe_installer_dir` backs a "check now" button in a settings UI, so a mistyped path or a
disconnected share is reported while the user is looking at the field.
"""

import contextlib
import logging
import platform
import re
import subprocess
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from packaging import version

logger = logging.getLogger(__name__)

UPDATE_AVAILABLE_MSG = (
    "An updated version of the installer is available. Would you like to update now?"
    "\n\nThis will launch the installer and exit the application."
)

if platform.architecture()[0] == "32bit":
    ARCH_STR = "-win32"
elif platform.architecture()[0] == "64bit":
    ARCH_STR = "-win64"
else:
    ARCH_STR = ""

# What an installer has to be called after the application's own name, for messages shown to a user.
_ARCH_HINT = f"[{ARCH_STR}]" if ARCH_STR else ""
INSTALLER_NAME_HINT = f"<X.Y.Z>+build.<N>{_ARCH_HINT}.msi (or .exe)"


class UpdateStatus(StrEnum):
    """What an update check found. A ``StrEnum``, so ``status == "unreachable"`` also works."""

    OFF = "off"
    """No folder is configured: the feature is off and nothing was touched."""
    UNREACHABLE = "unreachable"
    """The folder could not be read — a share that is not connected, a mistyped path, no permission.
    The one status worth showing at startup: while it lasts, no update will ever be offered."""
    NO_INSTALLER = "no_installer"
    """The folder was read and holds nothing named to the convention. Normal for a folder that was
    just set up, so not a problem to nag about."""
    UNCOMPARABLE = "uncomparable"
    """The running version could not be parsed, so nothing can be newer than it. A build defect."""
    UP_TO_DATE = "up_to_date"
    AVAILABLE = "available"
    """A newer installer is there. Only :func:`describe_installer_dir` reports this one: a check
    that finds an update asks, and comes back as DECLINED or ACCEPTED."""
    DECLINED = "declined"
    """A newer installer was offered and the user said no."""
    ACCEPTED = "accepted"
    """The installer was launched. Launching ends the process, so a caller sees this status only
    when it does not — under a test double, say."""
    ERROR = "error"
    """Anything else, already logged. The application carries on."""


#: The statuses that mean the check could not do its job — the ones an application may want to
#: point out on its settings form. Everything else is either "fine" or "the user already knows".
PROBLEM_STATUSES = frozenset({UpdateStatus.UNREACHABLE, UpdateStatus.UNCOMPARABLE, UpdateStatus.ERROR})


@dataclass(frozen=True)
class UpdateCheck:
    """What :func:`check_for_update` did, for a caller that wants to say so somewhere quiet."""

    status: UpdateStatus
    note: str = ""
    """One line, meant for a status label rather than a dialog; empty when there is nothing to say
    (OFF, UP_TO_DATE)."""
    installer: Path | None = None
    """The newest installer found, when one was."""

    @property
    def problem(self) -> bool:
        """True when the check could not do its job (see :data:`PROBLEM_STATUSES`)."""
        return self.status in PROBLEM_STATUSES


def _show_update_popup(window: Any | None = None) -> bool:
    try:
        if window is not None and (
            hasattr(window, "metaObject") or "PySide6" in str(type(window)) or "PyQt" in str(type(window))
        ):
            # Qt is an optional extra (see pyproject.toml), so it is normally absent from the
            # environment the checker sees; the branch is only reached with a Qt window.
            from PySide6.QtWidgets import QMessageBox  # pyright: ignore[reportMissingImports]

            reply = QMessageBox.question(
                window,
                "Update Available",
                UPDATE_AVAILABLE_MSG,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            return reply == QMessageBox.StandardButton.Yes

        import tkinter as tk
        from tkinter import messagebox

        temp_root = None
        if window is None:
            temp_root = tk.Tk()
            temp_root.withdraw()
            parent_window = None
        else:
            parent_window = window

        answer = messagebox.askyesno(
            title="Update Available",
            message=UPDATE_AVAILABLE_MSG,
            **(
                {"parent": parent_window}
                if parent_window is not None and hasattr(parent_window, "winfo_exists")
                else {}
            ),
        )

        if temp_root:
            temp_root.destroy()

        return answer
    except Exception as exc:  # noqa: BLE001 - a broken GUI toolkit must not block the update
        logger.error("Error displaying update popup: %s", exc)
        return True


# Installer version in filenames: "X.Y.Z" plus an optional PEP 440 pre-release marker
# (e.g. "-beta.1", parsed by packaging as 2.8.0b1) and the mandatory "+build.N" suffix.
# The marker charset excludes "-" so the arch suffix ("-win64") is never swallowed.
_INSTALLER_VERSION_REGEX = r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?\+build\.\d+"


def _get_installer_version(script_name: str, installer_path: Path) -> str | None:
    pattern = rf"^{re.escape(script_name)}-({_INSTALLER_VERSION_REGEX})({ARCH_STR})?\.(msi|exe)$"
    match = re.fullmatch(pattern, installer_path.name)
    return match.group(1) if match else None


def _parse_installer_version(version_text: str) -> version.Version | None:
    """Parse a filename version, returning None for strings packaging cannot interpret."""
    try:
        return version.parse(version_text)
    except version.InvalidVersion:
        logger.warning("Ignoring installer with unparseable version %r.", version_text)
        return None


def newest_installer(script_name: str, installer_dir: Path) -> tuple[Path | None, version.Version | None]:
    """The highest-versioned installer for ``script_name`` in the folder, as ``(path, version)``.

    ``(None, None)`` when the folder holds none — anything whose name does not follow
    ``<script_name>-{INSTALLER_NAME_HINT}`` is ignored, including a name carrying a version a
    person can read but ``packaging`` cannot compare. Raises ``FileNotFoundError`` if the folder
    itself cannot be read; that is the case worth telling a user about, so it is not swallowed
    here.
    """
    if not installer_dir.is_dir():
        raise FileNotFoundError(f"Invalid installer directory: {installer_dir}")

    candidates: list[tuple[version.Version, Path]] = [
        (parsed_ver, file)
        for file in installer_dir.iterdir()
        if file.is_file()
        and file.suffix.lower() in {".msi", ".exe"}
        and (inst_ver := _get_installer_version(script_name, file))
        and (parsed_ver := _parse_installer_version(inst_ver))
    ]

    if not candidates:
        return None, None

    latest_ver, latest_installer = max(candidates, key=lambda item: item[0])
    return latest_installer, latest_ver


def _check_for_updated_installer(script_name: str, installer_dir: Path, current_version: str) -> Path | None:
    current_ver = version.parse(current_version)
    latest_installer, latest_ver = newest_installer(script_name, installer_dir)

    # The two are set together or not at all, but only checking both says so to a type checker.
    if latest_installer is None or latest_ver is None:
        logger.info("No MSI or EXE installers found in the directory.")
        return None

    if latest_ver > current_ver:
        logger.info("Found updated installer: %s", latest_installer)
        return latest_installer

    logger.info("No updated MSI or EXE installer found.")
    return None


def _run_installer(installer_path: Path) -> None:
    try:
        logger.warning("Launching updated installer: %s", installer_path)
        if installer_path.suffix.lower() == ".msi":
            subprocess.Popen(["msiexec", "/i", str(installer_path)])
        else:
            subprocess.Popen([str(installer_path)])
    except Exception as exc:  # noqa: BLE001 - any launch failure must exit non-zero, not propagate
        logger.error("Error launching installer: %s", exc)
        sys.exit(1)
    else:
        sys.exit(0)


def auto_update(script_name: str, installer_dir: Path | None, current_version: str, window: Any | None = None) -> None:
    """Install the newest installer in the folder if it is newer than ``current_version``.

    The primitive. It raises on an unset or unreadable folder, and with no ``window`` it installs
    without asking. Most applications want :func:`check_for_update` instead.
    """
    if not installer_dir:
        raise ValueError("No installer directory specified.")

    new_installer = _check_for_updated_installer(script_name, installer_dir, current_version)
    if new_installer is None:
        return

    if window is not None and not _show_update_popup(window):
        logger.info("Skipping available update (%s) and running current version.", new_installer)
        return

    _run_installer(new_installer)


def _inspect(script_name: str, installer_dir: Path, current_version: str) -> tuple[UpdateStatus, Path | None, str]:
    """What the folder holds for this version, before anyone is asked anything.

    ``(status, installer, detail)`` — one of UNREACHABLE, NO_INSTALLER, UNCOMPARABLE, UP_TO_DATE
    or AVAILABLE; the installer is the newest one found (set from UNCOMPARABLE on), and the detail
    is the text of the problem for the two statuses that have one. Shared by the startup check and
    the "check now" button, so the two cannot disagree about what a folder holds.
    """
    try:
        latest_installer, latest_ver = newest_installer(script_name, installer_dir)
    except FileNotFoundError:
        return UpdateStatus.UNREACHABLE, None, "If it is a network share, it may need to be connected first."
    except OSError as exc:
        return UpdateStatus.UNREACHABLE, None, str(exc)

    # Both are set together or not at all, but only checking both says so to a type checker.
    if latest_installer is None or latest_ver is None:
        return UpdateStatus.NO_INSTALLER, None, ""

    try:
        running_ver = version.parse(current_version)
    except version.InvalidVersion:
        return (
            UpdateStatus.UNCOMPARABLE,
            latest_installer,
            f"this installation reports its own version as '{current_version}', which cannot be compared",
        )

    if latest_ver > running_ver:
        return UpdateStatus.AVAILABLE, latest_installer, ""
    return UpdateStatus.UP_TO_DATE, latest_installer, ""


def describe_installer_dir(
    script_name: str, installer_dir: Path | str | None, current_version: str
) -> tuple[bool, str]:
    """``(ok, message)`` describing what an installer folder holds — for a "check now" button.

    ``ok`` says the folder was read and holds a usable installer; ``False`` means there is
    something for the user to fix, and the message says what. The message is meant to be shown
    as-is.
    """
    if not installer_dir or not str(installer_dir).strip():
        return False, (
            "No folder is set. Choose the folder where new versions of this application are "
            "published, or leave it empty for no update check."
        )

    installer_dir = Path(str(installer_dir).strip())
    status, installer, detail = _inspect(script_name, installer_dir, current_version)
    if status is UpdateStatus.UNREACHABLE:
        return False, f"Cannot read this folder:\n{installer_dir}\n\n{detail}"
    if status is UpdateStatus.NO_INSTALLER:
        return False, (
            f"No installer for {script_name} in:\n{installer_dir}\n\nFiles there must be named "
            f"{script_name}-{INSTALLER_NAME_HINT} — anything else is ignored."
        )
    assert installer is not None  # every remaining status carries the installer it found
    if status is UpdateStatus.UNCOMPARABLE:
        return False, f"Found {installer.name}\n\nBut {detail}, so no update will be offered."
    if status is UpdateStatus.AVAILABLE:
        return True, (
            f"Found {installer.name}\n\nThat is newer than this version ({current_version}); "
            "it will be offered the next time this application starts."
        )
    return True, f"Found {installer.name}\n\nThis version ({current_version}) is up to date."


def check_for_update(
    script_name: str, installer_dir: Path | str | None, current_version: str, window: Any | None = None
) -> UpdateCheck:
    """Offer the newest installer in the folder, if there is one newer than this version.

    The entry point an application startup should call. Unlike :func:`auto_update` it:

    * treats an unset ``installer_dir`` as "update checks are off", touching nothing;
    * always asks before installing — a caller with no window yet (a tkinter application building
      its first window, say) gets a withdrawn root as the dialog's parent rather than a silent
      install. A GUI application that already has a main window should pass it, so the dialog is
      parented to it — a Qt application always should, or it gets a tkinter dialog;
    * never raises. A disconnected share, a folder of unrelated files, a version that cannot be
      parsed: all are logged and the application carries on starting.

    It returns an :class:`UpdateCheck` saying what happened. The distinction an application most
    likely wants is :attr:`UpdateCheck.problem` — the folder could not be read, or the check could
    not compare — which is worth a quiet note on the settings form, against everything else, which
    is not: a folder with nothing in it yet is the normal state right after it is set up, and a
    declined update was declined by the person looking at the screen.

    Accepting an update launches the installer and exits the process, so call this before building
    the UI, and never on a headless/scripted run where nobody can answer the dialog.
    """
    if not installer_dir or not str(installer_dir).strip():
        return UpdateCheck(UpdateStatus.OFF)

    folder = Path(str(installer_dir).strip())
    root = None
    try:
        status, installer, detail = _inspect(script_name, folder, current_version)
        if status is UpdateStatus.UNREACHABLE:
            logger.warning("Update check skipped: cannot read %s (%s)", folder, detail)
            return UpdateCheck(status, f"Update folder cannot be read: {folder}")
        if status is UpdateStatus.NO_INSTALLER:
            logger.info("No installer for %s in %s.", script_name, folder)
            return UpdateCheck(status, f"No installer for {script_name} in {folder}")
        if status is UpdateStatus.UNCOMPARABLE:
            logger.warning("Update check skipped: %s.", detail)
            return UpdateCheck(status, f"No update can be offered: {detail}", installer)
        if status is UpdateStatus.UP_TO_DATE:
            logger.info("No updated MSI or EXE installer found.")
            return UpdateCheck(status, "", installer)

        assert installer is not None  # AVAILABLE always carries one
        logger.info("Found updated installer: %s", installer)
        if window is None:
            import tkinter as tk

            root = tk.Tk()
            root.withdraw()
            window = root
        if not _show_update_popup(window):
            logger.info("Skipping available update (%s) and running current version.", installer)
            return UpdateCheck(UpdateStatus.DECLINED, f"{installer.name} is available and was not installed", installer)
        _run_installer(installer)  # exits the process; the return below is for a caller that stubbed it
        return UpdateCheck(UpdateStatus.ACCEPTED, f"Launched {installer.name}", installer)
    except Exception as exc:  # noqa: BLE001 - a failed update check must never stop the application
        logger.warning("Update check skipped: %s", exc)
        return UpdateCheck(UpdateStatus.ERROR, f"Update check failed: {exc}")
    finally:
        if root is not None:
            with contextlib.suppress(Exception):
                root.destroy()
