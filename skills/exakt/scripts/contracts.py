"""Deterministic validation for Exakt's portable JSON contract subset.

This module intentionally implements only the JSON Schema keywords used by
the bundled Exakt v1 schemas.  Schema definitions are checked before use, and
an unsupported validation keyword is an error rather than an ignored hint.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
SCHEMA_BASE_ID = "urn:exakt:schema:"
DEFAULT_SCHEMA_ROOT = Path(__file__).resolve().parents[1] / "schemas"

# These are fail-closed parser/validator resource limits, not JSON Schema
# semantics. Values outside them raise ContractError instead of leaking a
# Python runtime exception or consuming unbounded resources.
MAX_PARSED_INTEGER_DIGITS = 100_000
MAX_JSON_NESTING_DEPTH = 256
MAX_JSON_NODES = 100_000
MAX_LOCAL_REFERENCE_HOPS = 256
_INTEGER_PARSE_CHUNK_DIGITS = 256
_MAX_JSON_INTEGER_ABS_EXCLUSIVE = 10 ** MAX_PARSED_INTEGER_DIGITS

_ANNOTATION_KEYWORDS = {"title", "description"}
_SUPPORTED_KEYWORDS = {
    "$schema",
    "$id",
    "$defs",
    "$ref",
    "type",
    "properties",
    "required",
    "additionalProperties",
    "items",
    "minItems",
    "uniqueItems",
    "minLength",
    "pattern",
    "minimum",
    "enum",
    "const",
    *_ANNOTATION_KEYWORDS,
}
_SUPPORTED_TYPES = {"object", "array", "string", "integer", "boolean", "null"}
_SCHEMA_NAME = re.compile(r"^[a-z][a-z0-9-]*-v[1-9][0-9]*$")
_LOCAL_REF = re.compile(r"^#/\$defs/([A-Za-z][A-Za-z0-9_]*)$")
_SIMPLE_PROPERTY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ContractError(ValueError):
    """A document does not satisfy a known Exakt contract."""


class SchemaDefinitionError(ContractError):
    """A Exakt schema uses malformed or unsupported contract semantics."""


def _reject_constant(value: str) -> None:
    raise ContractError(f"invalid JSON constant: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"invalid JSON: duplicate object key {key!r}")
        result[key] = value
    return result


def _parse_json_integer(text: str) -> int:
    """Parse large JSON integers without Python's process-global digit limit."""
    negative = text.startswith("-")
    digits = text[1:] if negative else text
    if len(digits) > MAX_PARSED_INTEGER_DIGITS:
        raise ContractError(
            "invalid JSON: integer exceeds portable parser limit of "
            f"{MAX_PARSED_INTEGER_DIGITS} digits"
        )

    value = 0
    first_chunk_size = len(digits) % _INTEGER_PARSE_CHUNK_DIGITS
    if first_chunk_size == 0:
        first_chunk_size = min(len(digits), _INTEGER_PARSE_CHUNK_DIGITS)
    offset = 0
    while offset < len(digits):
        chunk_size = first_chunk_size if offset == 0 else _INTEGER_PARSE_CHUNK_DIGITS
        chunk = digits[offset : offset + chunk_size]
        value = (value * (10 ** len(chunk))) + int(chunk)
        offset += chunk_size
    return -value if negative else value


def loads_json_document(text: str) -> Any:
    """Parse JSON while rejecting duplicate keys and non-finite numbers."""
    try:
        document = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_int=_parse_json_integer,
        )
        _ensure_json_domain(document)
        return document
    except ContractError:
        raise
    except (
        json.JSONDecodeError,
        TypeError,
        ValueError,
        OverflowError,
        RecursionError,
    ) as error:
        if isinstance(error, json.JSONDecodeError):
            detail = f"line {error.lineno} column {error.colno}: {error.msg}"
        else:
            detail = str(error)
        raise ContractError(f"invalid JSON: {detail}") from error


def load_json_document(path: str | Path) -> Any:
    """Load one strict JSON document from a real file."""
    document_path = Path(path)
    try:
        text = document_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ContractError(f"cannot read JSON document {document_path}: {error}") from error
    return loads_json_document(text)


def _json_key(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (ValueError, OverflowError, RecursionError):
        if isinstance(value, int) and not isinstance(value, bool):
            sign = "negative" if value < 0 else "non-negative"
            return f"<integer {sign}, bit_length={abs(value).bit_length()}>"
        return f"<{_instance_type(value)} outside safe display limits>"


def _json_fingerprint(value: Any) -> tuple[Any, ...]:
    """Return a hashable JSON-type-sensitive structural identity."""
    if value is None:
        return ("null",)
    if isinstance(value, bool):
        return ("boolean", value)
    if isinstance(value, int):
        return ("integer", value)
    if isinstance(value, str):
        return ("string", value)
    if isinstance(value, list):
        return ("array", tuple(_json_fingerprint(item) for item in value))
    if isinstance(value, dict):
        return (
            "object",
            tuple((key, _json_fingerprint(value[key])) for key in sorted(value)),
        )
    return ("outside-json-domain", type(value).__name__)


def _json_equal(left: Any, right: Any) -> bool:
    return _json_fingerprint(left) == _json_fingerprint(right)


def _instance_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _ensure_json_domain(value: Any, path: str = "$") -> None:
    """Reject Python-only or over-budget values without recursive traversal."""
    stack: list[tuple[str, Any, str, int]] = [("visit", value, path, 0)]
    active_containers: set[int] = set()
    visited_nodes = 0

    while stack:
        operation, current, current_path, depth = stack.pop()
        if operation == "leave":
            active_containers.remove(current)
            continue

        visited_nodes += 1
        if visited_nodes > MAX_JSON_NODES:
            raise ContractError(
                f"{current_path}: JSON value exceeds portable node limit of "
                f"{MAX_JSON_NODES}"
            )
        if depth > MAX_JSON_NESTING_DEPTH:
            raise ContractError(
                f"{current_path}: JSON nesting depth exceeds portable limit of "
                f"{MAX_JSON_NESTING_DEPTH}"
            )

        if isinstance(current, int) and not isinstance(current, bool):
            if abs(current) >= _MAX_JSON_INTEGER_ABS_EXCLUSIVE:
                raise ContractError(
                    f"{current_path}: integer exceeds portable limit of "
                    f"{MAX_PARSED_INTEGER_DIGITS} digits"
                )
            continue
        if current is None or isinstance(current, (bool, str)):
            continue
        if isinstance(current, list):
            identity = id(current)
            if identity in active_containers:
                raise ContractError(f"{current_path}: cyclic container is not valid JSON")
            active_containers.add(identity)
            stack.append(("leave", identity, current_path, depth))
            for index in range(len(current) - 1, -1, -1):
                stack.append(
                    ("visit", current[index], f"{current_path}[{index}]", depth + 1)
                )
            continue
        if isinstance(current, dict):
            invalid_key_types = sorted(
                {_instance_type(key) for key in current if not isinstance(key, str)}
            )
            if invalid_key_types:
                raise ContractError(
                    f"{current_path}: JSON object keys must be strings, got "
                    f"{invalid_key_types[0]}"
                )
            identity = id(current)
            if identity in active_containers:
                raise ContractError(f"{current_path}: cyclic container is not valid JSON")
            active_containers.add(identity)
            stack.append(("leave", identity, current_path, depth))
            for key in reversed(sorted(current)):
                stack.append(
                    (
                        "visit",
                        current[key],
                        _path_property(current_path, key),
                        depth + 1,
                    )
                )
            continue
        raise ContractError(
            f"{current_path}: value of type {_instance_type(current)} is outside the "
            "Exakt JSON contract domain"
        )


def _path_property(path: str, property_name: str) -> str:
    if _SIMPLE_PROPERTY.fullmatch(property_name):
        return f"{path}.{property_name}"
    return f"{path}[{json.dumps(property_name, ensure_ascii=False)}]"


def _schema_types(schema: dict[str, Any], path: str) -> tuple[str, ...]:
    declared = schema.get("type")
    if isinstance(declared, str):
        types = (declared,)
    elif isinstance(declared, list) and declared:
        if any(not isinstance(item, str) for item in declared):
            raise SchemaDefinitionError(f"{path}.type must contain only strings")
        if len(set(declared)) != len(declared):
            raise SchemaDefinitionError(f"{path}.type contains duplicates")
        types = tuple(declared)
    else:
        raise SchemaDefinitionError(f"{path}.type must be a string or non-empty array")
    unsupported = sorted(set(types) - _SUPPORTED_TYPES)
    if unsupported:
        raise SchemaDefinitionError(f"{path}.type uses unsupported types: {unsupported}")
    return types


def _require_keyword_type(
    schema: dict[str, Any],
    keyword: str,
    allowed_type: str,
    schema_types: tuple[str, ...],
    path: str,
) -> None:
    if keyword in schema and allowed_type not in schema_types:
        rendered_types = " or ".join(schema_types)
        raise SchemaDefinitionError(
            f"{path}.{keyword} is only valid for {allowed_type} schema, "
            f"not {rendered_types} schema"
        )


def _root_definitions(root: dict[str, Any], path: str) -> dict[str, Any]:
    definitions = root.get("$defs", {})
    if not isinstance(definitions, dict) or any(
        not isinstance(name, str) for name in definitions
    ):
        raise SchemaDefinitionError(f"{path}.$defs must be an object")
    return definitions


def _check_reference_budget(reference_stack: tuple[str, ...], path: str) -> None:
    if len(reference_stack) >= MAX_LOCAL_REFERENCE_HOPS:
        raise SchemaDefinitionError(
            f"{path} exceeds portable local reference hop limit of "
            f"{MAX_LOCAL_REFERENCE_HOPS}"
        )


def _check_schema_node(
    schema: Any,
    path: str,
    root: dict[str, Any],
    reference_stack: tuple[str, ...] = (),
    is_root: bool = False,
    inherited_types: tuple[str, ...] | None = None,
) -> None:
    if not isinstance(schema, dict):
        raise SchemaDefinitionError(f"{path} must be a schema object")

    unknown = sorted(set(schema) - _SUPPORTED_KEYWORDS)
    if unknown:
        raise SchemaDefinitionError(
            f"{path} uses unsupported schema keywords: {', '.join(unknown)}"
        )

    for keyword in ("$schema", "$id", "title", "description"):
        if keyword in schema and not isinstance(schema[keyword], str):
            raise SchemaDefinitionError(f"{path}.{keyword} must be a string")
    if not is_root and "$id" in schema:
        raise SchemaDefinitionError(
            f"{path} uses nested resource identifiers, which the Exakt subset "
            "does not implement"
        )
    if not is_root and "$schema" in schema:
        raise SchemaDefinitionError(
            f"{path} uses nested schema dialects, which the Exakt subset "
            "does not implement"
        )

    if "$defs" in schema:
        _root_definitions(schema, path)

    if "$ref" in schema:
        _check_reference_budget(reference_stack, path)
        ref = schema["$ref"]
        if not isinstance(ref, str) or _LOCAL_REF.fullmatch(ref) is None:
            raise SchemaDefinitionError(f"{path}.$ref must be a local $defs reference")
        if ref in reference_stack:
            raise SchemaDefinitionError(f"{path} contains cyclic local reference {ref}")
        name = _LOCAL_REF.fullmatch(ref).group(1)
        definitions = _root_definitions(root, path)
        if name not in definitions:
            raise SchemaDefinitionError(f"{path}.$ref targets missing definition {name!r}")
        siblings = {key: value for key, value in schema.items() if key != "$ref"}
        _check_schema_node(
            definitions[name],
            f"{path}.$ref({name})",
            root,
            (*reference_stack, ref),
        )
        if siblings:
            referenced_types = _resolved_schema_types(
                definitions[name],
                f"{path}.$ref({name})",
                root,
                (*reference_stack, ref),
            )
            _check_schema_node(
                siblings,
                path,
                root,
                reference_stack,
                is_root=is_root,
                inherited_types=referenced_types,
            )
        return

    if "type" in schema:
        schema_types = _schema_types(schema, path)
    elif inherited_types is not None:
        schema_types = inherited_types
    else:
        raise SchemaDefinitionError(f"{path}.type must be declared or inherited from $ref")
    _require_keyword_type(schema, "properties", "object", schema_types, path)
    _require_keyword_type(schema, "required", "object", schema_types, path)
    _require_keyword_type(schema, "additionalProperties", "object", schema_types, path)
    _require_keyword_type(schema, "items", "array", schema_types, path)
    _require_keyword_type(schema, "minItems", "array", schema_types, path)
    _require_keyword_type(schema, "uniqueItems", "array", schema_types, path)
    _require_keyword_type(schema, "minLength", "string", schema_types, path)
    _require_keyword_type(schema, "pattern", "string", schema_types, path)
    _require_keyword_type(schema, "minimum", "integer", schema_types, path)

    if "object" in schema_types:
        if inherited_types is None and "additionalProperties" not in schema:
            raise SchemaDefinitionError(
                f"{path} object schema requires explicit additionalProperties"
            )
        if "additionalProperties" in schema and not isinstance(
            schema["additionalProperties"], bool
        ):
            raise SchemaDefinitionError(
                f"{path}.additionalProperties must be boolean in the Exakt subset"
            )
        properties = schema.get("properties", {})
        if not isinstance(properties, dict) or any(
            not isinstance(name, str) for name in properties
        ):
            raise SchemaDefinitionError(f"{path}.properties must be an object")
        required = schema.get("required", [])
        if (
            not isinstance(required, list)
            or any(not isinstance(name, str) for name in required)
            or len(set(required)) != len(required)
        ):
            raise SchemaDefinitionError(
                f"{path}.required must be an array of unique strings"
            )
        missing_properties = sorted(set(required) - set(properties))
        if inherited_types is None and missing_properties:
            raise SchemaDefinitionError(
                f"{path}.required names undefined properties: {missing_properties}"
            )
        for name in sorted(properties):
            _check_schema_node(
                properties[name],
                _path_property(f"{path}.properties", name),
                root,
                reference_stack,
            )

    if "array" in schema_types:
        if inherited_types is None and "items" not in schema:
            raise SchemaDefinitionError(f"{path} array schema requires items")
        if "items" in schema:
            _check_schema_node(
                schema["items"],
                f"{path}.items",
                root,
                reference_stack,
            )
        if "minItems" in schema and (
            not isinstance(schema["minItems"], int)
            or isinstance(schema["minItems"], bool)
            or schema["minItems"] < 0
        ):
            raise SchemaDefinitionError(f"{path}.minItems must be a non-negative integer")
        if "uniqueItems" in schema and not isinstance(schema["uniqueItems"], bool):
            raise SchemaDefinitionError(f"{path}.uniqueItems must be boolean")

    if "minLength" in schema and (
        not isinstance(schema["minLength"], int)
        or isinstance(schema["minLength"], bool)
        or schema["minLength"] < 0
    ):
        raise SchemaDefinitionError(f"{path}.minLength must be a non-negative integer")
    if "pattern" in schema:
        if not isinstance(schema["pattern"], str):
            raise SchemaDefinitionError(f"{path}.pattern must be a string")
        try:
            re.compile(schema["pattern"])
        except (re.error, OverflowError) as error:
            raise SchemaDefinitionError(f"{path}.pattern is invalid: {error}") from error
    if "minimum" in schema and (
        not isinstance(schema["minimum"], int)
        or isinstance(schema["minimum"], bool)
    ):
        raise SchemaDefinitionError(f"{path}.minimum must be an integer")

    if "enum" in schema:
        enum = schema["enum"]
        if not isinstance(enum, list) or not enum:
            raise SchemaDefinitionError(f"{path}.enum must be a non-empty array")
        keys = [_json_fingerprint(item) for item in enum]
        if len(set(keys)) != len(keys):
            raise SchemaDefinitionError(f"{path}.enum contains duplicate values")

    if "$defs" in schema:
        definitions = _root_definitions(schema, path)
        for name in sorted(definitions):
            _check_schema_node(
                definitions[name],
                _path_property(f"{path}.$defs", name),
                root,
                reference_stack,
            )


def _resolved_schema_types(
    schema: dict[str, Any],
    path: str,
    root: dict[str, Any],
    reference_stack: tuple[str, ...] = (),
) -> tuple[str, ...]:
    if "type" in schema:
        return _schema_types(schema, path)
    ref = schema.get("$ref")
    if ref is None:
        raise SchemaDefinitionError(f"{path}.type must be declared or inherited from $ref")
    _check_reference_budget(reference_stack, path)
    if ref in reference_stack:
        raise SchemaDefinitionError(f"{path} contains cyclic local reference {ref}")
    match = _LOCAL_REF.fullmatch(ref) if isinstance(ref, str) else None
    if match is None:
        raise SchemaDefinitionError(f"{path}.$ref must be a local $defs reference")
    name = match.group(1)
    definitions = _root_definitions(root, path)
    if name not in definitions:
        raise SchemaDefinitionError(f"{path}.$ref targets missing definition {name!r}")
    return _resolved_schema_types(
        definitions[name],
        f"{path}.$ref({name})",
        root,
        (*reference_stack, ref),
    )


def _validate_instance(
    instance: Any,
    schema: dict[str, Any],
    root: dict[str, Any],
    path: str,
    reference_stack: tuple[str, ...] = (),
    inherited_types: tuple[str, ...] | None = None,
) -> None:
    if "$ref" in schema:
        _check_reference_budget(reference_stack, path)
        ref = schema["$ref"]
        if ref in reference_stack:
            raise SchemaDefinitionError(f"cyclic local schema reference: {ref}")
        match = _LOCAL_REF.fullmatch(ref) if isinstance(ref, str) else None
        if match is None:
            raise SchemaDefinitionError(f"invalid local schema reference: {ref!r}")
        name = match.group(1)
        definitions = _root_definitions(root, path)
        if name not in definitions:
            raise SchemaDefinitionError(
                f"{path}.$ref targets missing definition {name!r}"
            )
        referenced = definitions[name]
        next_stack = (*reference_stack, ref)
        _validate_instance(instance, referenced, root, path, next_stack)
        siblings = {key: value for key, value in schema.items() if key != "$ref"}
        if siblings:
            referenced_types = _resolved_schema_types(
                referenced,
                f"schema.$ref({name})",
                root,
                next_stack,
            )
            _validate_instance(
                instance,
                siblings,
                root,
                path,
                reference_stack,
                inherited_types=referenced_types,
            )
        return

    if "type" in schema:
        schema_types = _schema_types(schema, "schema")
    elif inherited_types is not None:
        schema_types = inherited_types
    else:
        raise SchemaDefinitionError("schema.type must be declared or inherited from $ref")
    actual_type = _instance_type(instance)
    if actual_type not in schema_types:
        expected = " or ".join(schema_types)
        raise ContractError(f"{path}: expected {expected}, got {actual_type}")

    if "const" in schema and not _json_equal(instance, schema["const"]):
        raise ContractError(
            f"{path}: expected constant {_json_key(schema['const'])}, got {_json_key(instance)}"
        )
    if "enum" in schema and not any(
        _json_equal(instance, option) for option in schema["enum"]
    ):
        raise ContractError(
            f"{path}: value {_json_key(instance)} is not in enum {_json_key(schema['enum'])}"
        )

    if actual_type == "object":
        properties = schema.get("properties", {})
        for required_name in sorted(schema.get("required", [])):
            if required_name not in instance:
                raise ContractError(
                    f"{_path_property(path, required_name)}: required field is missing"
                )
        unknown = sorted(set(instance) - set(properties))
        if unknown and schema.get("additionalProperties", True) is False:
            raise ContractError(f"{_path_property(path, unknown[0])}: unknown field")
        for name in sorted(set(instance) & set(properties)):
            _validate_instance(
                instance[name],
                properties[name],
                root,
                _path_property(path, name),
            )
    elif actual_type == "array":
        if len(instance) < schema.get("minItems", 0):
            raise ContractError(
                f"{path}: expected at least {_json_key(schema['minItems'])} items, "
                f"got {len(instance)}"
            )
        if schema.get("uniqueItems"):
            seen: set[tuple[Any, ...]] = set()
            for index, item in enumerate(instance):
                key = _json_fingerprint(item)
                if key in seen:
                    raise ContractError(f"{path}[{index}]: duplicate array item")
                seen.add(key)
        if "items" in schema:
            for index, item in enumerate(instance):
                _validate_instance(item, schema["items"], root, f"{path}[{index}]")
    elif actual_type == "string":
        if len(instance) < schema.get("minLength", 0):
            raise ContractError(
                f"{path}: expected at least {_json_key(schema['minLength'])} "
                "characters"
            )
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            raise ContractError(
                f"{path}: value {_json_key(instance)} does not match pattern "
                f"{schema['pattern']!r}"
            )
    elif actual_type == "integer" and instance < schema.get("minimum", instance):
        raise ContractError(
            f"{path}: value {_json_key(instance)} is below minimum "
            f"{_json_key(schema['minimum'])}"
        )


class ContractRegistry:
    """Load, verify, select, and apply Exakt schemas from one directory."""

    def __init__(self, schema_root: str | Path = DEFAULT_SCHEMA_ROOT):
        self.schema_root = Path(schema_root)
        self._by_id: dict[str, dict[str, Any]] = {}
        self._by_filename: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        try:
            paths = sorted(self.schema_root.glob("*.json"), key=lambda path: path.name)
        except OSError as error:
            raise SchemaDefinitionError(
                f"cannot enumerate schema directory {self.schema_root}: {error}"
            ) from error
        if not paths:
            raise SchemaDefinitionError(f"no JSON schemas found in {self.schema_root}")

        for path in paths:
            try:
                schema = load_json_document(path)
            except ContractError as error:
                raise SchemaDefinitionError(f"{path.name}: {error}") from error
            if not isinstance(schema, dict):
                raise SchemaDefinitionError(f"{path.name}: schema must be an object")
            expected_id = SCHEMA_BASE_ID + path.stem
            if schema.get("$schema") != SCHEMA_DIALECT:
                raise SchemaDefinitionError(
                    f"{path.name}: unsupported or missing $schema dialect"
                )
            if schema.get("$id") != expected_id:
                raise SchemaDefinitionError(
                    f"{path.name}: $id must be stable value {expected_id!r}"
                )
            version = path.stem
            if _SCHEMA_NAME.fullmatch(version) is None:
                raise SchemaDefinitionError(f"{path.name}: invalid versioned schema name")
            _check_schema_node(schema, path.name, schema, is_root=True)
            version_schema = schema.get("properties", {}).get("schema_version")
            if version_schema != {"type": "string", "const": version}:
                raise SchemaDefinitionError(
                    f"{path.name}: schema_version must be a string const matching filename"
                )
            if "schema_version" not in schema.get("required", []):
                raise SchemaDefinitionError(
                    f"{path.name}: schema_version must be required"
                )
            if expected_id in self._by_id:
                raise SchemaDefinitionError(f"duplicate schema $id: {expected_id}")
            self._by_id[expected_id] = schema
            self._by_filename[path.name] = schema

    def _select(self, selector: str) -> dict[str, Any]:
        if not isinstance(selector, str):
            raise ContractError("schema selector must be a string")
        if selector in self._by_id:
            return self._by_id[selector]
        filename = selector if selector.endswith(".json") else f"{selector}.json"
        if filename in self._by_filename:
            return self._by_filename[filename]
        raise ContractError(f"unknown schema: {selector}")

    def validate(self, document: Any, schema_id: str | None = None) -> str:
        """Validate a value and return the stable schema ID that was applied."""
        _ensure_json_domain(document)
        if schema_id is None:
            if not isinstance(document, dict):
                raise ContractError("$: expected object with schema_version")
            version = document.get("schema_version")
            if not isinstance(version, str):
                raise ContractError("$.schema_version: required string is missing")
            filename = f"{version}.json"
            schema = self._by_filename.get(filename)
            if schema is None:
                raise ContractError(f"unknown schema version: {version}")
        else:
            schema = self._select(schema_id)

        _validate_instance(document, schema, schema, "$")
        return schema["$id"]

    def validate_file(self, path: str | Path, schema_id: str | None = None) -> str:
        return self.validate(load_json_document(path), schema_id)


def validate_document(
    document: Any,
    schema_id: str | None = None,
    schema_root: str | Path = DEFAULT_SCHEMA_ROOT,
) -> str:
    """Validate a document using a fresh registry."""
    return ContractRegistry(schema_root).validate(document, schema_id)


def validate_json_file(
    path: str | Path,
    schema_id: str | None = None,
    schema_root: str | Path = DEFAULT_SCHEMA_ROOT,
) -> str:
    """Load and validate a JSON file using a fresh registry."""
    return ContractRegistry(schema_root).validate_file(path, schema_id)
