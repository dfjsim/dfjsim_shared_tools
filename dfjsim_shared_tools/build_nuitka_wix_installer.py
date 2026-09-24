"""Shared Nuitka + WiX MSI builder for dfjsim applications."""

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .qt_auto_compiler import compile_ui_if_needed

WIX_EULA_ID = "wix7"
WIX_KNOWN_PATHS = [
    Path("C:/Program Files/WiX Toolset v7.0/bin/wix.exe"),
    Path("C:/Program Files/WiX Toolset v6.0/bin/wix.exe"),
]

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_WIX_DIR = PACKAGE_DIR / "wix"

# Test suites are not followed into the build. A project whose dependencies hold a real module matching these
# patterns (e.g. ``jinja2.tests``, imported by Jinja2 itself) replaces them with
# ``[tool.wix-nuitka].default_nofollow_imports``: Nuitka applies ``--nofollow-import-to`` before any include option,
# so nothing else can bring such a module back.
DEFAULT_NOFOLLOW_IMPORTS = ("*.tests", "*.test.*", "*_tests")


@dataclass(frozen=True)
class BuildConfig:
    project_root: Path
    pyproject_path: Path
    app_name: str
    app_version: str
    app_version_short: str
    app_manufacturer: str
    app_upgrade_code: str
    allow_downgrades: bool
    remove_existing_products: bool
    copy_files: tuple[str, ...]
    build_only: bool
    installer_only: bool
    nuitka_plugins: tuple[str, ...]
    nuitka_qt_plugins: tuple[str, ...]
    nuitka_include_packages: tuple[str, ...]
    nuitka_include_package_data: tuple[str, ...]
    nuitka_include_modules: tuple[str, ...]
    nuitka_default_nofollow_imports: tuple[str, ...]
    nuitka_nofollow_imports: tuple[str, ...]
    nuitka_exe_icon: str | None
    nuitka_onefile: bool
    nuitka_windows_console_mode: str | None
    entry_point: str
    ui_compile_pairs: tuple[tuple[str, str], ...]
    wix_setup_template: Path
    wix_ui_template: Path
    msi_output: Path


def _arch_suffix() -> str:
    if platform.architecture()[0] == "32bit":
        return "-win32"
    if platform.architecture()[0] == "64bit":
        return "-win64"
    return ""


def _normalize_str_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        text = value.strip()
        return (text,) if text else ()
    if isinstance(value, list | tuple):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return (str(value).strip(),)


def _resolve_project_path(project_root: Path, path_text: str | Path) -> Path:
    path = Path(path_text).expanduser()
    return path.resolve() if path.is_absolute() else (project_root / path).resolve()


def _resolve_template_path(project_root: Path, raw_value: Any, default_path: Path) -> Path:
    if not raw_value:
        return default_path
    return _resolve_project_path(project_root, str(raw_value))


def _collect_ui_compile_pairs(nuitka_config: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    pairs: list[tuple[str, str]] = []

    ui_file = nuitka_config.get("ui_file")
    ui_py_file = nuitka_config.get("ui_py_file")
    if ui_file or ui_py_file:
        if not ui_file or not ui_py_file:
            raise ValueError("[tool.wix-nuitka] requires both 'ui_file' and 'ui_py_file' when either is set")
        pairs.append((str(ui_file), str(ui_py_file)))

    raw_pairs = nuitka_config.get("ui_compile_pairs", [])
    if raw_pairs:
        if not isinstance(raw_pairs, list):
            raise ValueError("[tool.wix-nuitka].ui_compile_pairs must be a list of tables")
        for index, raw_pair in enumerate(raw_pairs, start=1):
            if not isinstance(raw_pair, dict):
                # ValueError, not TypeError: every [tool.wix-nuitka] validation failure is
                # reported the same way so callers can catch a single exception type.
                raise ValueError(f"ui_compile_pairs entry #{index} must be a table")  # noqa: TRY004
            ui_value = raw_pair.get("ui") or raw_pair.get("ui_file")
            py_value = raw_pair.get("py") or raw_pair.get("py_file")
            if not ui_value or not py_value:
                raise ValueError(f"ui_compile_pairs entry #{index} must contain both ui and py values")
            pairs.append((str(ui_value), str(py_value)))

    deduped: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for pair in pairs:
        if pair in seen:
            continue
        deduped.append(pair)
        seen.add(pair)
    return tuple(deduped)


# MSI ProductVersion must be numeric-only: "major.minor.build" with an optional fourth
# field (which Windows Installer ignores for upgrade comparisons).
_MSI_VERSION_PATTERN = re.compile(r"^\d+(\.\d+){1,3}$")


def _resolve_msi_version(app_version: str, msi_config: dict[str, Any]) -> str:
    """Resolve the numeric version passed to WiX as AppVersion.

    Prefers an explicit ``[tool.msi].version`` (required for pre-release versions such as
    ``2.8.0-beta.1+build.20260703``, which cannot be represented in an MSI ProductVersion).
    Falls back to ``[project].version`` with any ``+local`` suffix stripped, preserving the
    historical behavior for plain ``X.Y.Z+build.N`` versions.
    """
    explicit_version = msi_config.get("version")
    if explicit_version is not None:
        resolved = str(explicit_version).strip()
        if not _MSI_VERSION_PATTERN.match(resolved):
            raise ValueError(
                f"[tool.msi].version {resolved!r} is not a valid MSI ProductVersion; "
                "use numeric fields only, e.g. '2.8.0.20260703'."
            )
        return resolved

    derived = app_version.split("+")[0] if "+" in app_version else app_version
    if not _MSI_VERSION_PATTERN.match(derived):
        raise ValueError(
            f"Cannot derive a valid MSI ProductVersion from [project].version {app_version!r} "
            f"(got {derived!r}). Pre-release markers cannot appear in an MSI version; set an "
            "explicit numeric [tool.msi].version, e.g. '2.8.0.20260703'."
        )
    return derived


def load_build_config(project_root: Path | None = None) -> BuildConfig:
    resolved_project_root = (project_root or Path.cwd()).resolve()
    pyproject_path = resolved_project_root / "pyproject.toml"
    if not pyproject_path.exists():
        raise FileNotFoundError(f"Could not find pyproject.toml in {resolved_project_root}")

    with pyproject_path.open("rb") as handle:
        pyproject_data = tomllib.load(handle)

    project = pyproject_data["project"]
    tool = pyproject_data.get("tool", {})
    msi_config = tool.get("msi", {})
    wix_build_config = tool.get("wix-build", {})
    wix_nuitka_config = tool.get("wix-nuitka", {})

    app_name = str(project["name"])
    app_version = str(project["version"])
    app_version_short = _resolve_msi_version(app_version, msi_config)
    app_manufacturer = project.get("authors", [{}])[0].get("name", "SCT") if project.get("authors") else "SCT"
    app_upgrade_code = str(msi_config["upgrade_code"])

    wix_setup_template = _resolve_template_path(
        resolved_project_root,
        wix_build_config.get("setup_wxs"),
        DEFAULT_WIX_DIR / "MyWix_InstallerSetup.wxs",
    )
    wix_ui_template = _resolve_template_path(
        resolved_project_root,
        wix_build_config.get("ui_wxs"),
        DEFAULT_WIX_DIR / "MyWixUI_InstallDir.wxs",
    )

    return BuildConfig(
        project_root=resolved_project_root,
        pyproject_path=pyproject_path,
        app_name=app_name,
        app_version=app_version,
        app_version_short=app_version_short,
        app_manufacturer=str(app_manufacturer),
        app_upgrade_code=app_upgrade_code,
        allow_downgrades=bool(msi_config.get("allow_downgrades", False)),
        remove_existing_products=bool(msi_config.get("remove_existing_products", True)),
        copy_files=_normalize_str_tuple(wix_build_config.get("copy_files")),
        build_only=bool(wix_build_config.get("build_only", False)),
        installer_only=bool(wix_build_config.get("installer_only", False)),
        nuitka_plugins=_normalize_str_tuple(wix_nuitka_config.get("plugins")),
        nuitka_qt_plugins=_normalize_str_tuple(wix_nuitka_config.get("qt_plugins")),
        nuitka_include_packages=_normalize_str_tuple(wix_nuitka_config.get("include_packages")),
        nuitka_include_package_data=_normalize_str_tuple(wix_nuitka_config.get("include_package_data")),
        nuitka_include_modules=_normalize_str_tuple(wix_nuitka_config.get("include_modules")),
        nuitka_default_nofollow_imports=(
            DEFAULT_NOFOLLOW_IMPORTS
            if wix_nuitka_config.get("default_nofollow_imports") is None
            else _normalize_str_tuple(wix_nuitka_config.get("default_nofollow_imports"))
        ),
        nuitka_nofollow_imports=_normalize_str_tuple(wix_nuitka_config.get("nofollow_imports")),
        nuitka_exe_icon=(
            str(wix_nuitka_config.get("exe_icon")) if wix_nuitka_config.get("exe_icon") is not None else None
        ),
        nuitka_onefile=bool(wix_nuitka_config.get("onefile", False)),
        nuitka_windows_console_mode=(
            str(wix_nuitka_config.get("windows_console_mode"))
            if wix_nuitka_config.get("windows_console_mode") is not None
            else None
        ),
        entry_point=str(wix_nuitka_config.get("entry_point", f"{app_name}.py")),
        ui_compile_pairs=_collect_ui_compile_pairs(wix_nuitka_config),
        wix_setup_template=wix_setup_template,
        wix_ui_template=wix_ui_template,
        msi_output=resolved_project_root / "dist" / f"{app_name}-{app_version}{_arch_suffix()}.msi",
    )


def _ensure_ui_generated(config: BuildConfig) -> None:
    for ui_path_text, py_path_text in config.ui_compile_pairs:
        compile_ui_if_needed(
            _resolve_project_path(config.project_root, ui_path_text),
            _resolve_project_path(config.project_root, py_path_text),
        )


def _find_wix_executable() -> str:
    for env_name in ("WIX", "WIX_PATH", "WIX_EXE"):
        value = os.environ.get(env_name)
        if not value:
            continue
        candidate = Path(value).expanduser()
        if candidate.is_dir():
            candidate = candidate / "wix.exe"
        candidate = candidate.resolve()
        if candidate.exists():
            return str(candidate)

    which = shutil.which("wix") or shutil.which("wix.exe")
    if which:
        return which

    for candidate in WIX_KNOWN_PATHS:
        if candidate.exists():
            return str(candidate)

    raise FileNotFoundError(
        "Could not find wix.exe. Install the WiX Toolset packages first (for example with tools/install_wix_tools.ps1)."
    )


def _detect_nuitka_dist_dir(config: BuildConfig, entry_point: str) -> Path | None:
    candidates = [
        config.project_root / f"{config.app_name}.dist",
        config.project_root / (Path(entry_point).with_suffix("").name + ".dist"),
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            return candidate.resolve()

    dist_dirs = sorted(config.project_root.glob("*.dist"), key=lambda path: path.stat().st_mtime, reverse=True)
    return dist_dirs[0].resolve() if dist_dirs else None


def _copy_extra_files(config: BuildConfig, destination_dir: Path, copy_files: tuple[str, ...]) -> None:
    if not copy_files:
        return

    destination_dir.mkdir(parents=True, exist_ok=True)
    for item in copy_files:
        source_path = _resolve_project_path(config.project_root, item)
        dest_path = destination_dir / source_path.name
        if source_path.is_dir():
            print(f"[INFO] Copying directory {source_path} to {dest_path}")
            if dest_path.exists():
                shutil.rmtree(dest_path)
            shutil.copytree(source_path, dest_path)
        else:
            print(f"[INFO] Copying {source_path} to {destination_dir}")
            shutil.copy2(source_path, dest_path)


def _nofollow_args(config: BuildConfig) -> list[str]:
    """The ``--nofollow-import-to`` argument: default patterns (test suites) plus the project's own."""
    patterns = (*config.nuitka_default_nofollow_imports, *config.nuitka_nofollow_imports)
    return [f"--nofollow-import-to={','.join(patterns)}"] if patterns else []


def build_nuitka(
    config: BuildConfig,
    copy_files: tuple[str, ...] | None = None,
    entry_point: str | None = None,
) -> Path | None:
    print("[INFO] Building with Nuitka...")
    _ensure_ui_generated(config)

    effective_copy_files = config.copy_files if copy_files is None else copy_files
    effective_entry_point_raw = entry_point or config.entry_point
    effective_entry_point = _resolve_project_path(config.project_root, effective_entry_point_raw)

    if config.nuitka_onefile:
        build_mode = "--onefile"
        output_dir = config.project_root / "dist"
        output_dir.mkdir(parents=True, exist_ok=True)
        nuitka_args_extra = [f"--output-dir={output_dir}"]
        print("[INFO] Building in onefile mode (single executable)")
    else:
        build_mode = "--standalone"
        nuitka_args_extra = []
        print("[INFO] Building in standalone mode (folder with dependencies)")

    nuitka_args = [
        build_mode,
        *_nofollow_args(config),
        f"--output-filename={config.app_name}.exe",
        *nuitka_args_extra,
    ]

    if config.nuitka_windows_console_mode:
        allowed_modes = {"disable", "attach", "force", "hide"}
        mode = str(config.nuitka_windows_console_mode).lower()
        if mode in allowed_modes:
            nuitka_args.append(f"--windows-console-mode={mode}")
        else:
            print(
                f"[WARN] Invalid windows_console_mode='{config.nuitka_windows_console_mode}'. "
                f"Allowed: {sorted(allowed_modes)}. Ignoring."
            )
    else:
        print("[INFO] No 'windows_console_mode' configured; using Nuitka default behavior.")

    if config.nuitka_qt_plugins:
        nuitka_args.append(f"--include-qt-plugins={','.join(config.nuitka_qt_plugins)}")

    for plugin in config.nuitka_plugins:
        nuitka_args.append(f"--enable-plugin={plugin}")
    for package in config.nuitka_include_packages:
        nuitka_args.append(f"--include-package={package}")
    for package in config.nuitka_include_package_data:
        nuitka_args.append(f"--include-package-data={package}")
    for module in config.nuitka_include_modules:
        nuitka_args.append(f"--include-module={module}")
    if config.nuitka_exe_icon:
        icon_path = _resolve_project_path(config.project_root, config.nuitka_exe_icon)
        nuitka_args.append(f"--windows-icon-from-ico={icon_path}")

    nuitka_args.append(str(effective_entry_point))
    nuitka_cmd = [sys.executable, "-m", "nuitka", "--msvc=latest", *nuitka_args]
    result = subprocess.run(nuitka_cmd, cwd=config.project_root, check=False)
    if result.returncode != 0:
        print("[ERROR] Nuitka build failed.")
        sys.exit(result.returncode)

    if config.nuitka_onefile:
        output_dir = config.project_root / "dist"
        onefile_exe = output_dir / f"{config.app_name}.exe"
        if not onefile_exe.exists():
            print(f"[ERROR] Could not locate onefile executable: {onefile_exe}")
            sys.exit(1)
        print(f"[INFO] Onefile executable created: {onefile_exe}")
        _copy_extra_files(config, output_dir, effective_copy_files)
        return None

    dist_dir = _detect_nuitka_dist_dir(config, effective_entry_point_raw)
    if not dist_dir:
        print("[ERROR] Could not locate Nuitka dist directory after build.")
        sys.exit(1)

    print(f"[INFO] Detected Nuitka dist directory: {dist_dir}")
    _copy_extra_files(config, dist_dir, effective_copy_files)
    return dist_dir


def build_cxfreeze(
    config: BuildConfig,
    copy_files: tuple[str, ...] | None = None,
    entry_point: str | None = None,
) -> Path | None:
    _ = copy_files
    _ = entry_point
    print("[INFO] Building with cx_Freeze...")
    result = subprocess.run([sys.executable, "cxfreeze_setup.py", "bdist"], cwd=config.project_root, check=False)
    if result.returncode != 0:
        print("[ERROR] cx_Freeze build failed.")
        sys.exit(result.returncode)
    return None


def create_installer(config: BuildConfig, include_source_dir: Path) -> None:
    print("[INFO] Creating installer with WiX...")
    config.msi_output.parent.mkdir(parents=True, exist_ok=True)
    wix_exe = _find_wix_executable()
    wix_cmd = [
        wix_exe,
        "build",
        "-acceptEula",
        WIX_EULA_ID,
        str(config.wix_setup_template),
        str(config.wix_ui_template),
        "-ext",
        "WixToolset.UI.wixext",
        "-ext",
        "WixToolset.Util.wixext",
        "-o",
        str(config.msi_output),
        "-d",
        f"AppId={config.app_name}",
        "-d",
        f"AppName={config.app_name}",
        "-d",
        f"Manufacturer={config.app_manufacturer}",
        "-d",
        f"AppVersion={config.app_version_short}",
        "-d",
        f"UpgradeCode={config.app_upgrade_code}",
        "-d",
        f"IncludeSourceDir={include_source_dir}",
        "-d",
        f"AllowDowngrades={'yes' if config.allow_downgrades else 'no'}",
        "-d",
        f"RemoveExistingProducts={'yes' if config.remove_existing_products else 'no'}",
    ]
    result = subprocess.run(wix_cmd, cwd=config.project_root, check=False)
    if result.returncode != 0:
        print("[ERROR] WiX build failed.")
        sys.exit(result.returncode)


def main() -> None:
    bootstrap_parser = argparse.ArgumentParser(add_help=False)
    bootstrap_parser.add_argument("--project-root", default=None, help=argparse.SUPPRESS)
    bootstrap_args, _remaining = bootstrap_parser.parse_known_args()

    config = load_build_config(
        _resolve_project_path(Path.cwd(), bootstrap_args.project_root) if bootstrap_args.project_root else None
    )

    parser = argparse.ArgumentParser(description="Build and/or package the application.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--nuitka", action="store_true", help="Build with Nuitka (default)")
    group.add_argument("--cxfreeze", action="store_true", help="Build with cx_Freeze")
    parser.add_argument("--project-root", default=None, help="Project root containing pyproject.toml")
    parser.add_argument(
        "--installer-only",
        action="store_true",
        default=config.installer_only,
        help=(
            "Create installer only, skip Nuitka/cx_Freeze build. Default: True if installer_only "
            "is set in pyproject.toml, otherwise False."
        ),
    )
    parser.add_argument(
        "--build-only",
        action="store_true",
        default=config.build_only,
        help=(
            "Build executable only, skip creating the WiX installer. Default: True if build_only "
            "is set in pyproject.toml, otherwise False."
        ),
    )
    parser.add_argument("--copy-files", nargs="*", help="List of files to copy to the dist directory")
    parser.add_argument("--entry-point", default=None, help="Entry point Python file")
    args = parser.parse_args()

    if args.project_root:
        config = load_build_config(_resolve_project_path(Path.cwd(), args.project_root))

    copy_files = tuple(args.copy_files) if args.copy_files is not None else config.copy_files
    entry_point = args.entry_point or config.entry_point
    build_func = build_cxfreeze if args.cxfreeze else build_nuitka

    if args.installer_only:
        dist_dir = _detect_nuitka_dist_dir(config, entry_point)
        if not dist_dir:
            print("[ERROR] Cannot determine IncludeSourceDir; run the build first or remove --installer-only.")
            sys.exit(1)
        create_installer(config, dist_dir)
        return

    dist_dir = build_func(config, copy_files=copy_files, entry_point=entry_point)
    if args.build_only:
        if dist_dir is None and config.nuitka_onefile:
            print("[INFO] Onefile build complete. No installer created.")
        return

    if dist_dir is None:
        if config.nuitka_onefile:
            print("[WARN] Onefile mode does not support the WiX installer. Use --build-only instead.")
            sys.exit(1)
        dist_dir = config.project_root / f"{config.app_name}.dist"

    create_installer(config, dist_dir)


__all__ = [
    "BuildConfig",
    "build_cxfreeze",
    "build_nuitka",
    "create_installer",
    "load_build_config",
    "main",
]


if __name__ == "__main__":
    main()
