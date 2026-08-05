# SPDX-License-Identifier: GPL-3.0-or-later
"""Utility to auto-compile Qt .ui files to .py files when needed."""

import subprocess
from pathlib import Path

__all__ = ["compile_ui_if_needed"]


def compile_ui_if_needed(
    ui_file: str | Path,
    py_file: str | Path,
    compiler_cmd: str = "pyside6-uic",
) -> bool:
    """Compile ``ui_file`` to ``py_file`` if needed.

    Returns ``True`` when compilation was performed and ``False`` when the
    generated Python file was already up to date.
    """

    ui = Path(ui_file)
    py = Path(py_file)

    if not ui.exists():
        raise FileNotFoundError(f"UI file does not exist: {ui}")

    if not py.exists():
        print(f"Python UI file doesn't exist. Compiling {ui} -> {py}...")
        subprocess.run([compiler_cmd, str(ui), "-o", str(py)], check=True)
        return True

    if ui.stat().st_mtime > py.stat().st_mtime:
        print(f"UI file has been modified. Recompiling {ui} -> {py}...")
        subprocess.run([compiler_cmd, str(ui), "-o", str(py)], check=True)
        return True

    return False
