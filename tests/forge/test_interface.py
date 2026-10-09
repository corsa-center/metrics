"""The Forge ABC is the only forge type collectors may depend on."""

import ast
import inspect
from pathlib import Path

import pytest

from forge.github import GitHubForge
from forge.gitlab import GitLabForge
from forge.interface import Forge

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("cls", [GitHubForge, GitLabForge])
def test_concrete_forges_implement_the_whole_interface(cls):
    assert issubclass(cls, Forge)
    assert not inspect.isabstract(cls), sorted(cls.__abstractmethods__)
    assert isinstance(cls.platform, str) and cls.platform


@pytest.mark.parametrize("cls", [GitHubForge, GitLabForge])
def test_signatures_match_the_interface(cls):
    for name in Forge.__abstractmethods__:
        expected = inspect.signature(getattr(Forge, name))
        actual = inspect.signature(getattr(cls, name))
        assert list(actual.parameters) == list(expected.parameters), name
        for pname, param in expected.parameters.items():
            assert actual.parameters[pname].kind == param.kind, (name, pname)
            assert actual.parameters[pname].default == param.default, (name, pname)


def test_forge_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        Forge()


def _module_level_imports(path: Path):
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            yield node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name


@pytest.mark.parametrize(
    "path",
    sorted((REPO_ROOT / "collectors").rglob("*.py"))
    + [REPO_ROOT / "orchestrator.py", REPO_ROOT / "package_report.py"]
    + sorted((REPO_ROOT / "tools").glob("*.py"))
    + sorted((REPO_ROOT / "scripts").glob("*.py")),
    ids=lambda p: str(p.relative_to(REPO_ROOT)),
)
def test_nothing_outside_forge_imports_a_concrete_forge(path):
    concrete = {"forge.github", "forge.gitlab"}
    assert not concrete & set(_module_level_imports(path))
