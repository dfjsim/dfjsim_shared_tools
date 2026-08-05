# dfjsim_shared_tools

Shared build and runtime helpers for dfjsim Python desktop applications.

It hosts the reusable pieces that were previously duplicated across several desktop application
repositories, so there is one canonical implementation instead of a copy per project.

## What is included

The package currently provides:

- `dfjsim_shared_tools.build_nuitka_wix_installer`
  - shared Nuitka + WiX MSI build entry point
- `dfjsim_shared_tools.qt_auto_compiler`
  - helper for compiling Qt `.ui` files to Python when needed
- `dfjsim_shared_tools.config_loader`
  - reusable TOML config loading / deep merge helper
- `dfjsim_shared_tools.auto_update`
  - reusable MSI / EXE update check helper
- `dfjsim_shared_tools/wix/*.wxs`
  - default WiX template files used by the shared installer builder

## Typical consumer setup

A consuming application repository can pin this package to a Git tag with `uv`.

Example:

```toml
[project]
dependencies = [
    "nuitka",
    "packaging",
    "pydantic",
    "dfjsim_shared_tools",
]

[project.scripts]
build = "dfjsim_shared_tools.build_nuitka_wix_installer:main"

[tool.uv]
package = true

[tool.uv.sources]
dfjsim_shared_tools = { git = "https://github.com/dfjsim/dfjsim_shared_tools.git", tag = "v0.1.0" }
```

## Build configuration contract

The shared installer builder reads configuration from the consuming project's `pyproject.toml`.

### `[tool.msi]`

- `upgrade_code`
- `allow_downgrades`
- `remove_existing_products`

### `[tool.wix-build]`

- `copy_files`
- `build_only`
- `installer_only`
- `setup_wxs` (optional app-specific override)
- `ui_wxs` (optional app-specific override)

### `[tool.wix-nuitka]`

- `entry_point`
- `plugins`
- `qt_plugins`
- `include_packages`
- `include_package_data`
- `include_modules`
- `nofollow_imports`
- `exe_icon`
- `onefile`
- `windows_console_mode`
- `ui_file` (optional single Qt `.ui` source)
- `ui_py_file` (optional generated Python file for `ui_file`)
- `ui_compile_pairs` (optional list of `{ ui = "...", py = "..." }` entries)

## WiX prerequisites

The repository includes a helper script for installing the WiX Toolset packages with `winget`:

- `tools/install_wix_tools.ps1`

## Development notes

- Run build commands from the consuming application's repository root.
- The shared builder intentionally reads `pyproject.toml` from the current working directory rather than from this package directory.
- Consumer repos should pin to a released Git tag instead of tracking a floating branch.
