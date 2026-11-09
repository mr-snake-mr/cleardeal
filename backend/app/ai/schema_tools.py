"""Turn a Pydantic model into the schema Gemini actually wants.

Pydantic emits a faithful JSON Schema. Gemini accepts a *subset*, and its
documented guidance is not "send less, exactly as emitted" — it is:

  https://ai.google.dev/gemini-api/docs/structured-output
  - "To allow a property to be null, include `"null"` in the type array
    (e.g., `{"type": ["string", "null"]}`)."
  - "Reduce branching from optional fields, enums, and unions ... simplify
    `anyOf` unions"
  - "Very large or deeply nested schemas may be rejected." -> 400 INVALID_ARGUMENT
  - `minLength`, `maxLength`, `minProperties`, `maxProperties`,
    `uniqueItems`, `contains` "are not enforced ... the API automatically
    strips and ignores them"
  - `minItems`/`maxItems` are enforced, but "duplicate the item schema for each
    counted position"

`str | None` is the common case in this codebase (`quote`, `deadline`,
`paypal_merchant_id`). Pydantic emits it as `anyOf: [{type: string},
{type: null}]`; the documented form is `{"type": ["string", "null"]}`. That is
the difference this module exists to make, because a `400` here takes the whole
feature down and every test would still pass against a fake transport.

Constraints removed here are re-validated by Pydantic on the way back, which is
why removing them is safe rather than lossy.
"""

from __future__ import annotations

import copy
from typing import Any

from pydantic import BaseModel

# Stripped: unenforced by the server, or costly to the grammar state machine.
# `minItems`/`maxItems` are enforceable but duplicate the item schema per
# counted position, and Pydantic re-checks them anyway.
_DROP_KEYS = {
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
    "minProperties",
    "maxProperties",
    "uniqueItems",
    "contains",
    "minContains",
    "maxContains",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "default",
    "$schema",
    "additionalProperties",
}

_MAX_INLINE_DEPTH = 8


def _resolve_refs(node: Any, defs: dict[str, Any], depth: int = 0) -> Any:
    """Inline every `$ref`. Non-recursive by construction in our models.

    Depth is capped rather than unbounded: a genuinely recursive model would
    otherwise expand forever, and the honest outcome there is a loud error, not
    a silently truncated schema the model then fails to satisfy.
    """
    if depth > _MAX_INLINE_DEPTH:
        raise ValueError("schema nests too deeply to inline; flatten the model")

    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            name = ref.split("/")[-1]
            if name not in defs:
                raise ValueError(f"unresolvable $ref: {ref}")
            merged = copy.deepcopy(defs[name])
            # Sibling keys next to a $ref are the local overrides.
            for key, value in node.items():
                if key != "$ref":
                    merged[key] = value
            return _resolve_refs(merged, defs, depth + 1)
        return {key: _resolve_refs(value, defs, depth) for key, value in node.items()}
    if isinstance(node, list):
        return [_resolve_refs(item, defs, depth) for item in node]
    return node


def _collapse_nullable(node: Any) -> Any:
    """`anyOf: [X, {type: null}]` -> `X` with `null` added to its type."""
    if isinstance(node, list):
        return [_collapse_nullable(item) for item in node]
    if not isinstance(node, dict):
        return node

    parts = node.get("anyOf")
    if isinstance(parts, list) and len(parts) == 2:
        nulls = [p for p in parts if isinstance(p, dict) and p.get("type") == "null"]
        others = [p for p in parts if p not in nulls]
        if len(nulls) == 1 and len(others) == 1 and isinstance(others[0], dict):
            # Filtered like the fallback branch below. It used to copy the node
            # wholesale, which carried Pydantic's `default: null` through — and
            # `default` is not a key Gemini's Schema accepts, so every call with
            # a nullable field was a 400.
            collapsed = {k: v for k, v in node.items() if k not in _DROP_KEYS}
            collapsed.pop("anyOf", None)
            inner = _collapse_nullable(others[0])
            collapsed.update({k: v for k, v in inner.items() if k != "title"})
            existing = collapsed.get("type")
            if isinstance(existing, str):
                collapsed["type"] = [existing, "null"]
            elif isinstance(existing, list) and "null" not in existing:
                collapsed["type"] = [*existing, "null"]
            return collapsed

    return {
        key: _collapse_nullable(value)
        for key, value in node.items()
        if key not in _DROP_KEYS
    }


def generation_schema(model: type[BaseModel]) -> dict[str, Any]:
    """The schema to send. Structure preserved; ceremony removed."""
    raw = model.model_json_schema()
    defs = raw.get("$defs", {}) or {}
    inlined = _resolve_refs({k: v for k, v in raw.items() if k != "$defs"}, defs)
    return _collapse_nullable(inlined)
