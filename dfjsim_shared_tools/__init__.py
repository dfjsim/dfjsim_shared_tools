"""Shared helper package for dfjsim applications."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("dfjsim_shared_tools")
except PackageNotFoundError:
    __version__ = "0.0.0"

__all__ = ["__version__"]
