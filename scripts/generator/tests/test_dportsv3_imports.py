from __future__ import annotations

import ast
from pathlib import Path

import dportsv3


def test_dportsv3_has_no_runtime_import_from_v2_namespace() -> None:
    package_root = Path(dportsv3.__file__).resolve().parent

    for module_path in package_root.rglob("*.py"):
        tree = ast.parse(module_path.read_text(), filename=str(module_path))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "dports"
                    assert not alias.name.startswith("dports.")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert module != "dports"
                assert not module.startswith("dports.")
