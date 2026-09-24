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
dfjsim_shared_tools = { git = "https://github.com/dfjsim/dfjsim_shared_tools.git", tag = "v0.3.0" }
```

Requires Python 3.14, matching the applications that consume it.

### Qt is optional

`pyside6` is an **extra**, not a hard dependency. Building an installer does not need Qt, and a
consuming project that is not a Qt application would otherwise pull in several hundred MB of it and
then have to tell Nuitka not to bundle it.

Nothing here imports PySide6 at module level: `auto_update` imports `QMessageBox` inside the
function that shows the popup, guarded, and falls back to tkinter, while `qt_auto_compiler` only
shells out to the `pyside6-uic` executable. So the package imports and works without the extra.

A Qt application that wants `qt_auto_compiler` should depend on `dfjsim_shared_tools[qt]`.

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
- `nofollow_imports` (added to the default patterns below)
- `default_nofollow_imports` (optional; replaces the default `["*.tests", "*.test.*", "*_tests"]`, which keep test
  suites out of the build. Set it when a dependency holds a real module matching them, e.g. Jinja2's
  `jinja2.tests`: `default_nofollow_imports = ["*.tests.*", "*.test.*", "*_tests"]`. Nuitka applies
  `--nofollow-import-to` before any include option, so `include_modules` cannot bring such a module back.
  An empty list turns the defaults off.)
- `exe_icon`
- `onefile`
- `windows_console_mode`
- `ui_file` (optional single Qt `.ui` source)
- `ui_py_file` (optional generated Python file for `ui_file`)
- `ui_compile_pairs` (optional list of `{ ui = "...", py = "..." }` entries)

## Update checks

`dfjsim_shared_tools.auto_update` looks in a folder the application points it at — a shared drive,
typically — for an installer newer than the running version, and offers to run it. There is no
update server and nothing is sent anywhere: the protocol is a directory listing plus a filename
comparison, so the folder must hold files named

```
<AppName>-<X.Y.Z>+build.<N>[-win64].msi        (or .exe)
```

which is exactly what this package's builder produces from `[project].version`. That version must
therefore carry the `+build.<N>` tag at build time: it is what the running application reports and
compares, and an untagged version is older than every tagged build of the same `X.Y.Z`.

```python
from dfjsim_shared_tools.auto_update import check_for_update, describe_installer_dir

# At startup, before building the UI. Accepting an update launches the installer and exits.
outcome = check_for_update(app_name, configured_folder, running_version, window=main_window)
if outcome.problem:                 # the share is not connected, or the check cannot compare
    settings_form.note = outcome.note   # one line, next to the folder field — not a dialog

# Behind a "Check" button in a settings dialog.
ok, message = describe_installer_dir(app_name, configured_folder, running_version)
```

- `check_for_update()` is the entry point an application should use. An unset folder means the
  feature is off and nothing is touched; it always asks before installing, creating a withdrawn
  tkinter parent when the caller has no window yet; and it never raises, so a disconnected share
  cannot stop the application from starting. Never call it on a headless run — nobody is there to
  answer the dialog. It returns an `UpdateCheck` — `status` (an `UpdateStatus`: `off`,
  `unreachable`, `no_installer`, `uncomparable`, `up_to_date`, `declined`, `accepted`, `error`),
  a one-line `note` for a status label, and the `installer` it found. `problem` is true for the
  statuses that mean the check could not do its job (`unreachable`, `uncomparable`, `error`) —
  the ones worth a quiet note on the settings form. A folder with no build in it yet is
  `no_installer`, not a problem: it is the normal state right after the folder is set up, and
  nagging about it at every start would teach people to ignore the note.
- `auto_update()` is the primitive underneath: it raises on a bad folder, and with `window=None`
  it installs **without asking**.
- `describe_installer_dir()` returns `(ok, message)` for a settings UI, so a mistyped path or a
  disconnected share is reported while the user is looking at the field.
- `newest_installer()` returns the highest-versioned installer in a folder, for callers that want
  the raw answer.

Where the folder path itself is stored is the application's business. A consumer whose own
repository is public must keep an internal share path out of it entirely — a per-user setting
rather than a committed config file.

## WiX prerequisites

The repository includes a helper script for installing the WiX Toolset packages with `winget`:

- `tools/install_wix_tools.ps1`

## Development notes

- Run build commands from the consuming application's repository root.
- The shared builder intentionally reads `pyproject.toml` from the current working directory rather than from this package directory.
- Consumer repos should pin to a released Git tag instead of tracking a floating branch.
