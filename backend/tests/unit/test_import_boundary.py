"""The deterministic core must stay deterministic.

app/domain owns the booking state, validation, completeness checking and the
question policy. The central claim of this project is that those things are
decided by code, not by a language model -- see docs/architecture.md.

If app/domain ever imports the LLM client, an HTTP client or the web framework,
that boundary has quietly been crossed and the claim stops being true. This test
turns the architectural rule into a failing build.
"""

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

DOMAIN_DIR = Path(__file__).resolve().parents[2] / "app" / "domain"

# app/domain may import the standard library, pydantic and dateutil. Anything
# that reaches the network, or that would invert the dependency direction, is out.
FORBIDDEN_ROOTS = ("app.llm", "app.services", "app.api", "groq", "httpx", "fastapi")


def _imported_modules(path: Path) -> Iterator[str]:
    """Yield every absolute module name imported by a Python file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            # level > 0 is a relative import, which cannot escape app/domain.
            yield node.module


def _domain_files() -> list[Path]:
    return sorted(DOMAIN_DIR.rglob("*.py"))


@pytest.mark.parametrize("path", _domain_files(), ids=lambda p: p.name)
def test_domain_has_no_io_dependencies(path: Path) -> None:
    offenders = sorted(
        module
        for module in _imported_modules(path)
        if any(module == root or module.startswith(root + ".") for root in FORBIDDEN_ROOTS)
    )
    assert not offenders, (
        f"app/domain/{path.name} imports {offenders}.\n"
        "The deterministic core must not depend on the LLM, the network or the "
        "web framework. See docs/architecture.md."
    )


def test_domain_directory_exists() -> None:
    """Guards against the parametrised test silently collecting nothing."""
    assert DOMAIN_DIR.is_dir(), f"{DOMAIN_DIR} is missing"
    assert _domain_files(), "no Python files found in app/domain"
