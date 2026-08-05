"""Utilities for optional MSI/EXE self-update checks."""

import logging
import platform
import re
import subprocess
import sys
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


def _show_update_popup(window: Any | None = None) -> bool:
    try:
        if window is not None and (
            hasattr(window, "metaObject") or "PySide6" in str(type(window)) or "PyQt" in str(type(window))
        ):
            from PySide6.QtWidgets import QMessageBox

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


def _check_for_updated_installer(script_name: str, installer_dir: Path, current_version: str) -> Path | None:
    if not installer_dir.is_dir():
        raise FileNotFoundError(f"Invalid installer directory: {installer_dir}")

    current_ver = version.parse(current_version)
    candidates: list[tuple[version.Version, Path]] = [
        (parsed_ver, file)
        for file in installer_dir.iterdir()
        if file.is_file()
        and file.suffix.lower() in {".msi", ".exe"}
        and (inst_ver := _get_installer_version(script_name, file))
        and (parsed_ver := _parse_installer_version(inst_ver))
    ]

    if not candidates:
        logger.info("No MSI or EXE installers found in the directory.")
        return None

    latest_ver, latest_installer = max(candidates, key=lambda item: item[0])
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
    if not installer_dir:
        raise ValueError("No installer directory specified.")

    new_installer = _check_for_updated_installer(script_name, installer_dir, current_version)
    if new_installer is None:
        return

    if window is not None and not _show_update_popup(window):
        logger.info("Skipping available update (%s) and running current version.", new_installer)
        return

    _run_installer(new_installer)
