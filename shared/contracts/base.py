"""
MONTA — Contract Integrity Primitives
=======================================
* ``revalidate`` — the only sanctioned way to derive an updated contract.
  Pydantic's ``model_copy(update=...)`` skips validation, which let invalid
  enums and out-of-range confidences into the system; ``revalidate`` rebuilds
  the model through ``model_validate`` so every field constraint re-runs.
  ``tests/test_contract_integrity.py`` forbids ``model_copy(update=`` in
  Layers 3–7.
* ``FrozenDict`` / ``FrozenMapping`` — immutable, picklable mappings for dict
  fields inside frozen contracts (a frozen model holding a plain ``dict`` is
  still mutable through the dict).
* ``Text`` — bounded free text for reasoning/evidence fields so LLM output
  cannot bloat state or smuggle unbounded content to clients.
"""

from collections.abc import Mapping
from typing import Annotated, Any, TypeVar

from pydantic import AfterValidator, BaseModel, Field

M = TypeVar("M", bound=BaseModel)

MAX_TEXT = 2000
_CONTROL = {c: None for c in range(32) if c not in (9, 10)}


class FrozenDict(dict):
    """A ``dict`` that refuses mutation after construction but still pickles and deep-copies."""

    __slots__ = ()

    def _immutable(self, *args, **kwargs):
        raise TypeError("FrozenDict is immutable; build a new contract with revalidate()")

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _immutable

    def __reduce__(self):
        return (FrozenDict, (dict(self),))

    def __deepcopy__(self, memo):
        return self

    def __copy__(self):
        return self


def _freeze(value: Mapping) -> FrozenDict:
    return value if isinstance(value, FrozenDict) else FrozenDict(value)


FREEZE = AfterValidator(_freeze)
"""Use as ``Annotated[dict[str, T], FREEZE]`` on contract fields."""


def _clean_text(value: str) -> str:
    return value.translate(_CONTROL)


Text = Annotated[str, Field(max_length=MAX_TEXT), AfterValidator(_clean_text)]
NonEmptyText = Annotated[str, Field(min_length=1, max_length=MAX_TEXT), AfterValidator(_clean_text)]


def revalidate(model: M, **updates: Any) -> M:
    """Return a new, fully validated instance of ``type(model)`` with ``updates`` applied."""
    unknown = set(updates) - set(type(model).model_fields)
    if unknown:
        raise ValueError(f"{type(model).__name__} has no fields {sorted(unknown)}")
    data = {name: getattr(model, name) for name in type(model).model_fields}
    data.update(updates)
    return type(model).model_validate(data)
