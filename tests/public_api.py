"""Shared runtime checks for the annotations on a published module API."""

from __future__ import annotations

import inspect
from collections.abc import Iterator, Mapping
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


def public_type_hints(
    module: ModuleType,
) -> Iterator[tuple[str, Mapping[str, object]]]:
    """Every annotation-bearing public value and class member in ``module``."""
    seen: set[int] = set()
    for name in module.__all__:
        value = getattr(module, name)
        qualified = f"{module.__name__}.{name}"
        if inspect.isfunction(value) and value.__module__ == module.__name__:
            yield qualified, get_type_hints(value)
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

    fields = inspect.get_annotations(value, eval_str=True)
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
                yield f"{member_name}{suffix}", get_type_hints(function)


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
