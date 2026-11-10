"""The schema we send is not the schema Pydantic emits.

Source: https://ai.google.dev/gemini-api/docs/structured-output
- "To allow a property to be null, include `"null"` in the type array
  (e.g., `{"type": ["string", "null"]}`)."
- "Reduce branching from optional fields, enums, and unions ... simplify
  `anyOf` unions"
- "Very large or deeply nested schemas may be rejected."
- `minLength`, `maxLength`, `minProperties`, `maxProperties`, `uniqueItems`,
  `contains` are stripped by the server; `minItems`/`maxItems` are enforced but
  "duplicate the item schema for each counted position".

So the payload is flattened and simplified here, and the constraints that get
removed are re-checked by Pydantic on the way back in — which the client already
does. What this module must never do is drop a *structural* fact: a property
that exists in the model must exist in the payload, or the model is free to
invent a shape the validator will then reject for the wrong reason.
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel

from app.ai.schema_tools import generation_schema


class Leaf(BaseModel):
    text: str
    source: Literal["explicit", "inferred"]
    quote: str | None = None


class Nested(BaseModel):
    requirements: list[Leaf]
    note: str | None = None


def _walk(node, seen=None):
    """Yield every dict in the tree, so a test can assert over the whole shape."""
    if seen is None:
        seen = set()
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value, seen)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value, seen)


def test_no_refs_or_defs_survive():
    schema = generation_schema(Nested)
    blob = json.dumps(schema)

    assert "$defs" not in blob
    assert "$ref" not in blob


def test_a_nullable_field_uses_nullable_not_a_type_array():
    """`anyOf` must go, but `type` may not become a list to compensate.

    `responseSchema` is the protobuf `Schema` message, where `type` is a single
    enum value. A JSON-Schema type array is rejected by the live API before the
    model is ever reached:

        Unknown name "type" ... Proto field is not repeating, cannot start list

    Nullability has its own field. This test used to assert the array, which is
    how a 400 on every analysis call survived a green suite.
    """
    schema = generation_schema(Nested)
    note = schema["properties"]["note"]

    assert "anyOf" not in note
    assert note["type"] == "string"
    assert note["nullable"] is True


def test_no_type_is_ever_a_list_anywhere():
    """The proto constraint, checked across every real schema the product sends.

    One list-typed `type` anywhere in the tree is a 400 for the whole call, so a
    spot check on one field is not enough.
    """
    from app.ai.schemas import AgreementDraft, BriefAnalysis, ChangeAnalysis

    for model in (Nested, BriefAnalysis, AgreementDraft, ChangeAnalysis):
        for node in _walk(generation_schema(model)):
            if "type" in node:
                assert isinstance(node["type"], str), f"{model.__name__}: {node['type']!r}"


def test_no_anyof_anywhere_in_the_payload():
    assert not [node for node in _walk(generation_schema(Nested)) if "anyOf" in node]


def test_unenforced_and_costly_constraints_are_removed():
    """The server strips the first group and charges states for the second."""
    from pydantic import Field

    class Bounded(BaseModel):
        text: str = Field(min_length=3, max_length=300)
        items: list[str] = Field(min_length=1, max_length=40)

    schema = generation_schema(Bounded)
    blob = json.dumps(schema)

    for removed in ("minLength", "maxLength", "minItems", "maxItems"):
        assert removed not in blob, removed


def test_nested_objects_are_inlined_not_dropped():
    """Flattening must not delete structure, only resolvable references."""
    schema = generation_schema(Nested)
    leaf = schema["properties"]["requirements"]["items"]

    assert leaf["type"] == "object"
    assert set(leaf["properties"]) == {"text", "source", "quote"}
    assert leaf["properties"]["source"]["enum"] == ["explicit", "inferred"]


def test_required_fields_are_preserved():
    schema = generation_schema(Nested)
    assert set(schema["required"]) == {"requirements"}


def test_a_required_nullable_field_stays_required_and_nullable():
    """Marking it optional to dodge branching would change the contract."""
    from pydantic import Field

    class Strict(BaseModel):
        quote: str | None = Field(description="verbatim")

    schema = generation_schema(Strict)

    assert schema["required"] == ["quote"]
    assert schema["properties"]["quote"]["type"] == "string"
    assert schema["properties"]["quote"]["nullable"] is True


def test_the_real_analysis_schema_is_flat_and_keeps_its_fields():
    from app.ai.schemas import BriefAnalysis

    schema = generation_schema(BriefAnalysis)
    blob = json.dumps(schema)

    assert "$ref" not in blob and "$defs" not in blob and "anyOf" not in blob
    requirement = schema["properties"]["requirements"]["items"]
    assert set(requirement["properties"]) == {"text", "source", "quote"}


def test_no_stripped_key_survives_the_nullable_collapse():
    """The collapse path is not a licence to ship keywords the server rejects.

    Pydantic emits `default: null` beside the `anyOf` it emits for `str | None`.
    The collapse branch used to return the node it had copied, so that `default`
    survived while the fallback branch filtered it — and the one nullable field
    in `BriefAnalysis` is `Requirement.quote`, so the leak landed on every call.
    Gemini's `Schema` has no `default` key, which makes this a 400 on the
    product's entry point.
    """
    from app.ai.schema_tools import _DROP_KEYS
    from app.ai.schemas import AgreementDraft, BriefAnalysis

    for model in (Nested, BriefAnalysis, AgreementDraft):
        for node in _walk(generation_schema(model)):
            leaked = set(node) & _DROP_KEYS
            assert not leaked, (model.__name__, leaked)
