"""Deterministic dual identifiers for ASS Events."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import struct
import unicodedata
from typing import Any, Callable, Mapping, Sequence

import xxhash

from .constants import (
    EVENT_ID_INPUT_VERSION, EVENT_ID_STRATEGY_VERSION,
    TEXT_ID_INPUT_VERSION, TEXT_ID_STRATEGY_VERSION,
)
from .models import AssDiagnostic, AssDocument, AssEvent
from .profiles import EventIdPolicy
from .text import text_features

_HASH_ALGORITHM = "xxh3_64"
_HASH_SEED = 0
_EVENT_PREFIX = "e_"
_TEXT_PREFIX = "t_"


@dataclass(frozen=True)
class EventIdResult:
    items: Mapping[int, Mapping[str, Any]]
    duplicate_groups: tuple[Mapping[str, Any], ...]
    diagnostics: tuple[AssDiagnostic, ...]
    collision_count: int

    @property
    def has_collision(self) -> bool:
        return self.collision_count > 0


def event_id_strategy(policy: EventIdPolicy | None = None) -> dict[str, Any]:
    return {
        "name": EVENT_ID_STRATEGY_VERSION,
        "scope": "document",
        "hash_algorithm": _HASH_ALGORITHM,
        "seed": _HASH_SEED,
        "encoding": "utf-8",
        "prefix": _EVENT_PREFIX,
        "hex_length": 16,
        "input_logic_version": EVENT_ID_INPUT_VERSION,
        "input": "record_type_and_all_event_fields",
        "duplicate_policy": "stable_duplicate_ordinal",
    }


def text_id_strategy() -> dict[str, Any]:
    return {
        "name": TEXT_ID_STRATEGY_VERSION,
        "scope": "document",
        "hash_algorithm": _HASH_ALGORITHM,
        "seed": _HASH_SEED,
        "encoding": "utf-8",
        "prefix": _TEXT_PREFIX,
        "hex_length": 16,
        "input_logic_version": TEXT_ID_INPUT_VERSION,
        "normalization": "unicode_nfc_newlines_trim",
        "empty_text": "null",
    }


def canonicalize_segments(
    segments: Sequence[tuple[str, str]], *, logic_version: str = EVENT_ID_INPUT_VERSION,
) -> bytes:
    header = logic_version.encode("utf-8")
    result = bytearray(struct.pack(">I", len(header)))
    result.extend(header)
    result.extend(struct.pack(">I", len(segments)))
    for name, value in segments:
        name_bytes = name.encode("utf-8")
        value_bytes = value.encode("utf-8")
        result.extend(struct.pack(">I", len(name_bytes)))
        result.extend(name_bytes)
        result.extend(struct.pack(">Q", len(value_bytes)))
        result.extend(value_bytes)
    return bytes(result)


def hash_canonical_input(value: bytes, *, prefix: str = _EVENT_PREFIX) -> str:
    return prefix + xxhash.xxh3_64_hexdigest(value, seed=_HASH_SEED)


def normalize_visible_text(value: str) -> str:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    return unicodedata.normalize("NFC", normalized).strip()


def resolve_input_fields(event_format: Sequence[str], requested: Sequence[str]) -> tuple[str, ...]:
    available = tuple(str(item).casefold() for item in event_format)
    requested_names = tuple(str(item).casefold() for item in requested)
    unknown = sorted(set(requested_names) - set(available))
    if unknown:
        raise ValueError(f"Event ID fields are not present in Event Format: {', '.join(unknown)}")
    selected = set(requested_names or available)
    return tuple(item for item in available if item in selected)


def canonical_event_input(
    event: AssEvent, *, event_format: Sequence[str], mode: str = "fields",
    input_fields: Sequence[str] = (), plain_text: str | None = None,
    logic_version: str = EVENT_ID_INPUT_VERSION,
) -> tuple[bytes, tuple[str, ...], bool]:
    """Return canonical input; legacy modes remain available for v1-v3 readers/tests."""
    if mode == "visible_text":
        visible = text_features(event.text)["plain_text"] if plain_text is None else plain_text
        return canonicalize_segments((("text", visible),), logic_version=logic_version), ("text",), True
    if mode == "raw_text":
        return canonicalize_segments((("text", event.text),), logic_version=logic_version), ("text",), False
    if mode not in {"fields", "line_fields", "all_fields_v2"}:
        raise ValueError("Event ID mode is invalid")
    selected = resolve_input_fields(event_format, () if mode in {"line_fields", "all_fields_v2"} else input_fields)
    segments = tuple((name, event.fields.get(name, "")) for name in selected)
    if mode == "all_fields_v2":
        segments = (("record_type", event.record_type),) + segments
    return canonicalize_segments(segments, logic_version=logic_version), selected, False


def canonical_text_input(value: str) -> bytes:
    return canonicalize_segments(
        (("text", normalize_visible_text(value)),), logic_version=TEXT_ID_INPUT_VERSION,
    )


def recompute_event_id(
    event: AssEvent, *, event_format: Sequence[str], mode: str = "all_fields_v2",
    input_fields: Sequence[str] = (), plain_text: str | None = None,
) -> str:
    canonical, _, _ = canonical_event_input(
        event, event_format=event_format, mode=mode,
        input_fields=input_fields, plain_text=plain_text,
        logic_version=EVENT_ID_INPUT_VERSION,
    )
    return hash_canonical_input(canonical)


def recompute_text_id(value: str) -> str | None:
    normalized = normalize_visible_text(value)
    if not normalized:
        return None
    return hash_canonical_input(canonical_text_input(normalized), prefix=_TEXT_PREFIX)


def build_event_ids(
    document: AssDocument, policy: EventIdPolicy | None = None,
    *, hash_function: Callable[[bytes], str] = hash_canonical_input,
    text_hash_function: Callable[[bytes], str] | None = None,
) -> EventIdResult:
    """Build a full-fields content ID, visible-text ID, and unique source_ref."""
    del policy  # v4 IDs are a fixed schema contract rather than a per-run choice.
    text_hasher = text_hash_function or (
        lambda value: hash_canonical_input(value, prefix=_TEXT_PREFIX)
    )
    raw: list[dict[str, Any]] = []
    event_hash_inputs: dict[str, set[bytes]] = defaultdict(set)
    text_hash_inputs: dict[str, set[bytes]] = defaultdict(set)
    for event in document.events:
        visible = normalize_visible_text(text_features(event.text)["plain_text"])
        canonical, fields, _ = canonical_event_input(
            event, event_format=document.event_format, mode="all_fields_v2",
        )
        event_id = hash_function(canonical)
        text_canonical = canonical_text_input(visible) if visible else None
        text_id = text_hasher(text_canonical) if text_canonical is not None else None
        event_hash_inputs[event_id].add(canonical)
        if text_id is not None:
            text_hash_inputs[text_id].add(text_canonical)
        raw.append({
            "event": event, "canonical": canonical, "event_id": event_id,
            "text_canonical": text_canonical, "text_id": text_id,
            "visible": visible, "fields": fields,
        })

    event_collisions = {value for value, inputs in event_hash_inputs.items() if len(inputs) > 1}
    text_collisions = {value for value, inputs in text_hash_inputs.items() if len(inputs) > 1}
    diagnostics: list[AssDiagnostic] = []
    duplicate_groups: list[dict[str, Any]] = []
    occurrence: dict[str, int] = defaultdict(int)
    identical: dict[str, list[int]] = defaultdict(list)
    items: dict[int, dict[str, Any]] = {}
    for candidate in raw:
        event = candidate["event"]
        event_id = candidate["event_id"]
        text_id = candidate["text_id"]
        status = "generated"
        if event_id in event_collisions:
            status = "hash_collision"
            diagnostics.append(AssDiagnostic(
                "event_id_hash_collision",
                "Different Event content produced the same XXH3-64 value",
                severity="error", section="Events", line=event.line_number,
                evidence={"ordinal": event.ordinal, "event_id": event_id},
            ))
            event_id = None
        if text_id in text_collisions:
            diagnostics.append(AssDiagnostic(
                "text_id_hash_collision",
                "Different visible text produced the same XXH3-64 value",
                severity="error", section="Events", line=event.line_number,
                evidence={"ordinal": event.ordinal, "text_id": text_id},
            ))
            text_id = None
            status = "hash_collision"
        if event_id is None:
            duplicate_ordinal = 0
            source_ref = f"ordinal:{event.ordinal}"
        else:
            duplicate_ordinal = occurrence[event_id]
            occurrence[event_id] += 1
            source_ref = f"{event_id}#{duplicate_ordinal}"
            identical[event_id].append(event.ordinal)
        items[event.ordinal] = {
            "value": event_id,
            "event_id": event_id,
            "text_id": text_id,
            "source_ref": source_ref,
            "duplicate_ordinal": duplicate_ordinal,
            "status": status,
            "mode": "all_fields_v2",
            "algorithm": _HASH_ALGORITHM,
            "strategy": EVENT_ID_STRATEGY_VERSION,
            "logic_version": EVENT_ID_INPUT_VERSION,
            "input_fields": list(candidate["fields"]),
            "tags_removed": False,
            "duplicate_fallback": False,
            "text": {
                "value": text_id,
                "status": "generated" if text_id is not None else "skipped_empty_text",
                "algorithm": _HASH_ALGORITHM,
                "strategy": TEXT_ID_STRATEGY_VERSION,
                "logic_version": TEXT_ID_INPUT_VERSION,
                "normalization": "unicode_nfc_newlines_trim",
            },
        }
    for event_id, ordinals in identical.items():
        if len(ordinals) > 1:
            duplicate_groups.append({
                "kind": "duplicate_identical_event", "event_id": event_id,
                "ordinals": ordinals,
                "source_refs": [items[ordinal]["source_ref"] for ordinal in ordinals],
            })
    return EventIdResult(
        items=items, duplicate_groups=tuple(duplicate_groups),
        diagnostics=tuple(diagnostics),
        collision_count=len(event_collisions) + len(text_collisions),
    )
