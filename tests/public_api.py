"""Shared checks for what a module publishes, and for how it is annotated."""

from __future__ import annotations

import ast
import inspect
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path
from types import ModuleType
from typing import Any, TypeAliasType, get_args, get_type_hints


def contains_any(annotation: object, seen: frozenset[int] = frozenset()) -> bool:
    """Whether an annotation, including an explicit alias, contains ``Any``."""
    if annotation is Any:
        return True
    identity = id(annotation)
    if identity in seen:
        return False
    visited = seen | {identity}
    if isinstance(annotation, TypeAliasType):
        return contains_any(annotation.__value__, visited)
    return any(contains_any(argument, visited) for argument in get_args(annotation))


def hints_of(function: object) -> Mapping[str, object]:
    """``get_type_hints``, with the function's own type parameters in scope.

    A PEP 695 generic keeps its parameters in ``__type_params__``, and before
    CPython 3.12.4 ``get_type_hints`` did not put them in the namespace it
    evaluates a string annotation in. `flexi.config.section` is
    ``def section[T: BaseModel](...) -> T``, and under `from __future__ import
    annotations` that return type is the string ``"T"`` -- so resolving it
    raised ``NameError: name 'T' is not defined``.

    Which is not hypothetical: Ubuntu 24.04 LTS ships 3.12.3, that is the
    interpreter the `ubuntu-latest · Python 3.12` rows of the matrix resolve to,
    and all three of them were failing on it. The repo claims 3.12 support in
    `requires-python`, so the oldest 3.12 anybody is likely to have is the one
    that has to work.

    Passing them explicitly costs nothing on a newer interpreter, and
    `class_type_hints` below has always done exactly this for classes -- plain
    functions were the case that was missed.
    """
    parameters = {
        parameter.__name__: parameter
        for parameter in getattr(function, "__type_params__", ())
    }
    return get_type_hints(function, localns=parameters)


def public_type_hints(
    module: ModuleType,
) -> Iterator[tuple[str, Mapping[str, object]]]:
    """Every annotation-bearing public value and class member in ``module``."""
    seen: set[int] = set()
    for name in module.__all__:
        value = getattr(module, name)
        qualified = f"{module.__name__}.{name}"
        if inspect.isfunction(value) and value.__module__ == module.__name__:
            yield qualified, hints_of(value)
        elif inspect.isclass(value) and value.__module__ == module.__name__:
            yield from class_type_hints(value, qualified, seen)
        elif isinstance(value, TypeAliasType):
            yield qualified, {"value": value.__value__}


def class_type_hints(
    value: type[object], qualified: str, seen: set[int]
) -> Iterator[tuple[str, Mapping[str, object]]]:
    """Annotations owned by one class, its public members and nested classes."""
    identity = id(value)
    if identity in seen:
        return
    seen.add(identity)

    module_globals = vars(sys.modules[value.__module__])
    type_parameters = {
        parameter.__name__: parameter
        for parameter in getattr(value, "__type_params__", ())
    }
    fields = inspect.get_annotations(
        value,
        globals=module_globals,
        locals=module_globals | type_parameters,
        eval_str=True,
    )
    if fields:
        yield qualified, fields

    for name, member in vars(value).items():
        if name.startswith("_") and name != "__init__":
            continue
        member_name = f"{qualified}.{name}"
        if inspect.isclass(member) and member.__module__ == value.__module__:
            yield from class_type_hints(member, member_name, seen)
            continue
        for role, function in member_functions(member):
            if getattr(function, "__module__", None) == value.__module__:
                suffix = f".{role}" if role else ""
                yield f"{member_name}{suffix}", hints_of(function)


def member_functions(member: object) -> Iterator[tuple[str, object]]:
    """Functions represented by one class dictionary member."""
    if isinstance(member, property):
        for role, accessor in (
            ("getter", member.fget),
            ("setter", member.fset),
            ("deleter", member.fdel),
        ):
            if accessor is not None:
                yield role, accessor
    elif isinstance(member, staticmethod | classmethod):
        yield "", member.__func__
    elif inspect.isfunction(member):
        yield "", member


def module_statements(statements: list[ast.stmt]) -> Iterator[ast.stmt]:
    """Statements evaluated at module scope, including conditional branches.

    `flexi.theme`, `flexi.context` and `flexi.models.database.migrate` define
    public names inside a module-level ``if``, so a walk of the top level alone
    reads their APIs as smaller than they are.
    """
    for statement in statements:
        yield statement
        if isinstance(statement, ast.If):
            yield from module_statements(statement.body)
            yield from module_statements(statement.orelse)


def target_names(target: ast.expr) -> Iterator[str]:
    """Names assigned by one module-level target, including tuple unpacking."""
    if isinstance(target, ast.Name):
        yield target.id
    elif isinstance(target, ast.List | ast.Tuple):
        for item in target.elts:
            yield from target_names(item)


def locally_defined_public_names(module: ModuleType) -> set[str]:
    """Public values a module defines itself rather than imports."""
    path = Path(module.__file__ or "")
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for statement in module_statements(tree.body):
        if isinstance(statement, ast.AsyncFunctionDef | ast.ClassDef | ast.FunctionDef):
            if not statement.name.startswith("_"):
                found.add(statement.name)
        elif isinstance(statement, ast.TypeAlias):
            found.update(
                name
                for name in target_names(statement.name)
                if not name.startswith("_")
            )
        elif isinstance(statement, ast.AnnAssign | ast.Assign):
            targets = (
                statement.targets
                if isinstance(statement, ast.Assign)
                else [statement.target]
            )
            found.update(
                name
                for target in targets
                for name in target_names(target)
                if not name.startswith("_")
            )
    return found


def wildcard_names(module: ModuleType) -> set[str]:
    """Names a real wildcard import receives from ``module``."""
    namespace: dict[str, object] = {}
    exec(f"from {module.__name__} import *", namespace)  # noqa: S102
    return set(namespace) - {"__builtins__"}


def check_declared_api(module: ModuleType) -> None:
    """The ``__all__`` convention, asserted in the one place it is written down.

    A tuple, because a list is an API somebody can append to at runtime; no
    duplicates; and complete in both directions -- every public name the module
    defines is exported, and a wildcard import receives exactly those.

    Six test files ask this of their own package's leaves, so the rule lives
    here once and the lists of modules live with the packages they describe.
    """
    assert isinstance(module.__all__, tuple)
    assert len(module.__all__) == len(set(module.__all__))
    assert all(hasattr(module, name) for name in module.__all__)
    assert set(module.__all__) == locally_defined_public_names(module)
    assert wildcard_names(module) == set(module.__all__)


def check_public_annotations(module: ModuleType) -> None:
    """Every published annotation resolves, and none of them is ``Any``."""
    checked = list(public_type_hints(module))

    assert checked, f"{module.__name__} publishes nothing with an annotation"
    for qualified, hints in checked:
        assert hints, f"{qualified} has no annotations"
        assert not any(map(contains_any, hints.values())), qualified
