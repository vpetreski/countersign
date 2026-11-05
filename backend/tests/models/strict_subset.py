"""An independent check of the strict tool-use dialect, written from the platform docs (structured outputs, "JSON
Schema limitations" and "Schema complexity limits", which strict tool use shares; read 2026-11-04), not from the
adapter's code, so a test that runs it is not the implementation checking itself."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from typing import Any

ALLOWED = frozenset(
    {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "minItems",
        "enum",
        "const",
        "anyOf",
        "allOf",
        "$ref",
        "$defs",
        "definitions",
        "format",
        "default",
        "title",
        "description",
    }
)
TYPES = frozenset({"object", "array", "string", "integer", "number", "boolean", "null"})
FORMATS = frozenset(
    {"date-time", "time", "date", "duration", "email", "hostname", "uri", "ipv4", "ipv6", "uuid"}
)
MAX_STRICT_TOOLS = 20
MAX_OPTIONAL_PARAMETERS = 24
MAX_UNION_PARAMETERS = 16


def _primitive(value: object) -> bool:
    return value is None or isinstance(value, str | int | float | bool)


def _definitions(schema: Mapping[str, Any]) -> dict[str, Any]:
    return {f"#/{k}/{n}": v for k in ("$defs", "definitions") for n, v in (schema.get(k) or {}).items()}


def _objects(schema: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
    """Every schema node of the document, definitions included."""
    stack: list[Any] = [schema, *_definitions(schema).values()]
    while stack:
        node = stack.pop()
        if not isinstance(node, Mapping):
            continue
        yield node
        stack.extend((node.get("properties") or {}).values())
        if isinstance(node.get("items"), Mapping):
            stack.append(node["items"])
        stack.extend(node.get("anyOf") or [])
        stack.extend(node.get("allOf") or [])


def problems(schema: Mapping[str, Any]) -> list[str]:
    """Every way `schema` falls outside the strict dialect; empty when it is inside."""
    found: list[str] = []
    defs = _definitions(schema)

    def walk(node: object, path: str, *, in_all_of: bool = False) -> None:
        if not isinstance(node, Mapping):
            found.append(f"{path}: not a schema object")
            return
        found.extend(f"{path}: unsupported keyword {key!r}" for key in node if key not in ALLOWED)
        if "$ref" in node:
            ref = node["$ref"]
            if not isinstance(ref, str) or not ref.startswith("#/") or ref not in defs:
                found.append(f"{path}: external or unresolved $ref {ref!r}")
            if in_all_of:
                found.append(f"{path}: $ref inside allOf")
        type_ = node.get("type")
        types = type_ if isinstance(type_, list) else ([] if type_ is None else [type_])
        found.extend(f"{path}: unsupported type {t!r}" for t in types if t not in TYPES)
        if ("object" in types or "properties" in node) and node.get("additionalProperties") is not False:
            found.append(f"{path}: an object without additionalProperties: false")
        if "additionalProperties" in node and node["additionalProperties"] is not False:
            found.append(f"{path}: additionalProperties other than false")
        missing = set(node.get("required") or []) - set(node.get("properties") or {})
        if missing:
            found.append(f"{path}: required names unknown properties {sorted(missing)}")
        if "minItems" in node and (node["minItems"] not in (0, 1) or isinstance(node["minItems"], bool)):
            found.append(f"{path}: minItems {node['minItems']!r} (only 0 and 1)")
        if "format" in node and node["format"] not in FORMATS:
            found.append(f"{path}: unsupported string format {node['format']!r}")
        if "enum" in node and not (isinstance(node["enum"], list) and all(map(_primitive, node["enum"]))):
            found.append(f"{path}: enum values must be strings, numbers, booleans or null")
        if "const" in node and not _primitive(node["const"]):
            found.append(f"{path}: complex const")
        for name, sub in (node.get("properties") or {}).items():
            walk(sub, f"{path}.properties.{name}")
        if "items" in node:
            walk(node["items"], f"{path}.items")
        for i, sub in enumerate(node.get("anyOf") or []):
            walk(sub, f"{path}.anyOf[{i}]")
        for i, sub in enumerate(node.get("allOf") or []):
            walk(sub, f"{path}.allOf[{i}]", in_all_of=True)

    if schema.get("type") != "object":
        found.append("$: a tool's input_schema must be an object")
    walk({k: v for k, v in schema.items() if k not in ("$defs", "definitions")}, "$")
    for ref, sub in defs.items():
        walk(sub, ref)
    found.extend(_cycles(defs))
    return found


def _cycles(defs: Mapping[str, Any]) -> list[str]:
    def refs(node: object) -> Iterator[str]:
        if isinstance(node, Mapping):
            for k, v in node.items():
                if k == "$ref" and isinstance(v, str):
                    yield v
                else:
                    yield from refs(v)
        elif isinstance(node, list):
            for v in node:
                yield from refs(v)

    found: list[str] = []
    for start in defs:
        seen: set[str] = set()
        todo = [r for r in refs(defs[start]) if r in defs]
        while todo:
            ref = todo.pop()
            if ref == start:
                found.append(f"{start}: recursive schema")
                break
            if ref not in seen:
                seen.add(ref)
                todo.extend(r for r in refs(defs[ref]) if r in defs)
    return found


def optional_parameters(schema: Mapping[str, Any]) -> int:
    """Properties not listed in `required`, over every object of the document (the docs' "optional parameters")."""
    return sum(
        len(set(node.get("properties") or {}) - set(node.get("required") or []))
        for node in _objects(schema)
        if "properties" in node
    )


def union_parameters(schema: Mapping[str, Any]) -> int:
    """Properties typed with anyOf or a type array (the docs' "parameters with union types")."""
    return sum(
        1
        for node in _objects(schema)
        for prop in (node.get("properties") or {}).values()
        if isinstance(prop, Mapping) and ("anyOf" in prop or isinstance(prop.get("type"), list))
    )


def request_problems(tools: Sequence[Mapping[str, Any]]) -> list[str]:
    """The per-request limits of strict tool use, plus every tool's own problems."""
    strict = [t for t in tools if t.get("strict") is True]
    found = [f"{t['name']}: not strict" for t in tools if t.get("strict") is not True]
    for tool in strict:
        found.extend(f"{tool['name']} {p}" for p in problems(tool["input_schema"]))
    if len(strict) > MAX_STRICT_TOOLS:
        found.append(f"{len(strict)} strict tools (at most {MAX_STRICT_TOOLS})")
    optional = sum(optional_parameters(t["input_schema"]) for t in strict)
    if optional > MAX_OPTIONAL_PARAMETERS:
        found.append(f"{optional} optional parameters (at most {MAX_OPTIONAL_PARAMETERS})")
    unions = sum(union_parameters(t["input_schema"]) for t in strict)
    if unions > MAX_UNION_PARAMETERS:
        found.append(f"{unions} union-typed parameters (at most {MAX_UNION_PARAMETERS})")
    return found
