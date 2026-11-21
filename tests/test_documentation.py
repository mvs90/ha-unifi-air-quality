"""Require nonempty function docstrings across integration, tools and tests.

Source inspection includes methods, async functions and nested callbacks without
importing optional dependencies. This presence check does not replace review of
the descriptions' accuracy or completeness.
"""

import ast
from pathlib import Path


def test_every_function_has_a_documentation_header():
    """Every Python function exposes a nonempty code-level docstring header."""
    root = Path(__file__).parents[1]
    missing = []
    # Inspect source rather than imports so optional developer tools stay isolated.
    for directory in ("custom_components", "dev", "tests"):
        for path in (root / directory).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef)
                ) and not ast.get_docstring(node):
                    missing.append(f"{path.relative_to(root)}:{node.name}")
    assert not missing, missing
