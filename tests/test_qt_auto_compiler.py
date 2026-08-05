import subprocess
from pathlib import Path

from dfjsim_shared_tools.qt_auto_compiler import compile_ui_if_needed


def test_compile_ui_if_needed_skips_when_generated_file_is_current(tmp_path: Path) -> None:
    ui_file = tmp_path / "demo.ui"
    py_file = tmp_path / "demo.py"
    ui_file.write_text("<ui />", encoding="utf-8")
    py_file.write_text("# generated", encoding="utf-8")
    py_file.touch()

    assert compile_ui_if_needed(ui_file, py_file) is False


def test_compile_ui_if_needed_runs_compiler_when_output_missing(tmp_path: Path, monkeypatch) -> None:
    ui_file = tmp_path / "demo.ui"
    py_file = tmp_path / "demo.py"
    ui_file.write_text("<ui />", encoding="utf-8")

    calls: list[list[str]] = []

    def _fake_run(command: list[str], check: bool) -> subprocess.CompletedProcess:
        calls.append(command)
        py_file.write_text("# generated", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", _fake_run)

    assert compile_ui_if_needed(ui_file, py_file) is True
    assert calls == [["pyside6-uic", str(ui_file), "-o", str(py_file)]]
