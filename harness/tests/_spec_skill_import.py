"""Isolated loader for hs:spec / hs:shape skill scripts under harness/tests.

The skill scripts use bare sibling imports (``from encoding_utils import ...``,
``from frontmatter_parser import ...``), so their directory must be importable
as a flat path. But their module names (``spec_graph``, ``frontmatter_parser``,
``encoding_utils``) COLLIDE with the harness-internal docs-governance copies on
``harness/scripts`` that conftest already puts on ``sys.path``. A plain
``sys.path.insert`` would let whichever copy is imported (or cached) first win,
making the suite order-dependent and flaky under ``pytest -n auto``.

``load_skill_scripts`` loads a named set from an explicit directory with FULL
save/restore of ``sys.modules`` and ``sys.path`` around the load: the freshly
loaded modules capture references to each other at import time, so they keep
working after the global state is restored — and no harness test that imports
the ``harness/scripts`` copy later is polluted.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Dict, List


def load_skill_scripts(scripts_dir: Path, names: List[str]) -> Dict[str, object]:
    """Load ``names`` (in order) from ``scripts_dir`` under their bare module names,
    resolving inter-module sibling imports to the copies in ``scripts_dir``, then
    restore ``sys.modules`` / ``sys.path`` to their prior state.

    Load order matters: a module's dependencies must precede it so its
    ``from <sibling> import ...`` resolves to the sibling just placed.
    """
    scripts_dir = Path(scripts_dir)
    saved_mods = {n: sys.modules.get(n) for n in names}
    saved_path = list(sys.path)
    sys.path.insert(0, str(scripts_dir))
    for n in names:
        sys.modules.pop(n, None)
    loaded: Dict[str, object] = {}
    try:
        for n in names:
            src = scripts_dir / ("%s.py" % n)
            spec = importlib.util.spec_from_file_location(n, src)
            if spec is None or spec.loader is None:
                raise ImportError("cannot load %s from %s" % (n, src))
            module = importlib.util.module_from_spec(spec)
            sys.modules[n] = module
            spec.loader.exec_module(module)
            loaded[n] = module
    finally:
        for n in names:
            if saved_mods[n] is not None:
                sys.modules[n] = saved_mods[n]
            else:
                sys.modules.pop(n, None)
        sys.path[:] = saved_path
    return loaded
