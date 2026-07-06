"""Bootstrap helper for tests — loads update_infrastructure via importlib.

Import this FIRST in every test module before any `from update_infrastructure import ...`.
"""

import importlib
import sys
from pathlib import Path

_PKG = "update_infrastructure"
_REPO = Path(__file__).resolve().parents[1]

if _PKG not in sys.modules:
    sys.path.insert(0, str(_REPO.parent))
    spec = importlib.util.spec_from_file_location(
        _PKG,
        _REPO / "__init__.py",
        submodule_search_locations=[str(_REPO)],
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = _PKG
    mod.__path__ = [str(_REPO)]
    sys.modules[_PKG] = mod
    spec.loader.exec_module(mod)
