"""wowlab_core: local-only library over a World of Warcraft install.

Spec: docs/LAB_PLAN.md. Hard invariants L1-L8: AGENTS.md.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("wowlab-core")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
