"""Read, validate, export, index, and losslessly bundle ASS analysis JSON."""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping

from .constants import (
    ANALYSIS_BUNDLE_SCHEMA_VERSION,
    ANALYSIS_SCHEMA_VERSION,
    EVENT_ID_INPUT_VERSION,
    EVENT_ID_STRATEGY_VERSION,
    LEGACY_ANALYSIS_SCHEMA_VERSIONS,
    TEXT_ID_INPUT_VERSION, TEXT_ID_STRATEGY_VERSION,
)
from .event_ids import (
    canonical_event_input, canonical_text_input, event_id_strategy,
    hash_canonical_input, text_id_strategy,
)
from .models import AssEvent
from .profiles import EventIdPolicy


_EVENT_ID_RE = re.compile(r"e_[0-9a-f]{16}")
_TEXT_ID_RE = re.compile(r"t_[0-9a-f]{16}")
_SOURCE_REF_RE = re.compile(r"e_[0-9a-f]{16}#\d+")
_LEGACY_EVENT_ID_RE = re.compile(r"ass-event-\d{6,}")
_NULL_ID_STATUSES = {"skipped_empty_text", "duplicate_identical_event", "hash_collision"}


def validate_analysis_payload(
    payload: Mapping[str, Any], *, source_artifact_id: str | None = None,
    allow_legacy: bool = False,
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("analysis payload must be a JSON object")
    schema = payload.get("schema_version")
    accepted = {ANALYSIS_SCHEMA_VERSION}
    if allow_legacy:
        accepted.update(LEGACY_ANALYSIS_SCHEMA_VERSIONS)
    if schema not in accepted:
        raise ValueError("analysis payload has an unexpected schema version")
    for key in (
        "source", "document", "script_info", "project_garbage", "styles",
        "events", "fonts", "review_queue", "diagnostics", "toolchain",
    ):
        if key not in payload:
            raise ValueError(f"analysis payload is missing {key}")
    source = payload["source"]
    if not isinstance(source, Mapping) or not source.get("artifact_id"):
        raise ValueError("analysis source artifact ID is missing")
    if source_artifact_id and source.get("artifact_id") != source_artifact_id:
        raise ValueError("analysis output source artifact does not match")
    events = payload["events"]
    if not isinstance(events, Mapping):
        raise ValueError("analysis events must be an object")
    items = events.get("items")
    count = events.get("statistics", {}).get("event_count")
    if not isinstance(items, list) or count != len(items):
        raise ValueError("analysis event count is inconsistent")
    if schema == ANALYSIS_SCHEMA_VERSION:
        event_refs = _validate_v4(payload, events, items)
    elif schema == "ass-analysis-v3":
        event_refs = _validate_v3(payload, events, items)
    elif schema == "ass-analysis-v2":
        event_refs = _validate_v2(payload, events, items)
    else:
        event_refs = {
            item.get("event_id") for item in items
            if isinstance(item, Mapping) and item.get("event_id")
        }
    _validate_common_references(payload, events, event_refs)
    return deepcopy(dict(payload))


def load_analysis(
    value: Path | str | Mapping[str, Any], *, source_artifact_id: str | None = None,
    allow_legacy: bool = True,
) -> dict[str, Any]:
    payload = _load_json_value(value, "analysis")
    return validate_analysis_payload(
        payload, source_artifact_id=source_artifact_id, allow_legacy=allow_legacy,
    )


def serialize_analysis(payload: Mapping[str, Any], *, indent: int | None = 2) -> str:
    validated = validate_analysis_payload(payload, allow_legacy=True)
    return json.dumps(validated, ensure_ascii=False, indent=indent) + "\n"


def export_analysis(
    payload: Mapping[str, Any], target: Path | str, *, overwrite: bool = False,
    indent: int | None = 2,
) -> Path:
    return _atomic_export(serialize_analysis(payload, indent=indent), target, overwrite=overwrite)


def index_analysis_events(payload: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    validated = validate_analysis_payload(payload, allow_legacy=True)
    use_source_ref = validated["schema_version"] == ANALYSIS_SCHEMA_VERSION
    return {
        (item.get("source_ref") if use_source_ref else item.get("event_id")): item
        for item in validated["events"]["items"]
        if (item.get("source_ref") if use_source_ref else item.get("event_id")) is not None
    }


def get_analysis_event(
    payload_or_index: Mapping[str, Any], event_ref: str,
) -> dict[str, Any] | None:
    if "events" in payload_or_index:
        for item in payload_or_index.get("events", {}).get("items", []):
            if isinstance(item, Mapping) and (
                item.get("source_ref") == event_ref or item.get("event_id") == event_ref
            ):
                return deepcopy(dict(item))
        return None
    item = payload_or_index.get(event_ref)
    return deepcopy(dict(item)) if isinstance(item, Mapping) else None


def combine_analyses(
    values: list[Path | str | Mapping[str, Any]] | tuple[Path | str | Mapping[str, Any], ...],
) -> dict[str, Any]:
    documents = [load_analysis(value) for value in values]
    source_ids = [item["source"]["artifact_id"] for item in documents]
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("analysis bundle source artifact IDs must be unique")
    event_references = []
    document_index = []
    record_types: dict[str, int] = {}
    id_statuses: dict[str, int] = {}
    event_count = 0
    indexed_event_count = 0
    style_count = 0
    review_count = 0
    for document in documents:
        source_id = document["source"]["artifact_id"]
        events = document["events"]["items"]
        statistics = document["events"]["statistics"]
        event_count += len(events)
        style_count += len(document["styles"]["items"])
        review_count += len(document["review_queue"])
        for name, count in statistics.get("record_types", {}).items():
            record_types[name] = record_types.get(name, 0) + count
        for event in events:
            event_ref = event.get("source_ref", event.get("event_id"))
            status = event.get("id", {}).get("status", "legacy")
            id_statuses[status] = id_statuses.get(status, 0) + 1
            if event_ref is not None:
                indexed_event_count += 1
                event_references.append({
                    "source_artifact_id": source_id,
                    "source_ref": event_ref,
                })
        document_index.append({
            "source_artifact_id": source_id,
            "schema_version": document["schema_version"],
            "path_name": document["source"].get("path_name"),
            "event_count": len(events),
            "indexed_event_count": sum(
                event.get("source_ref", event.get("event_id")) is not None for event in events
            ),
            "style_count": len(document["styles"]["items"]),
            "review_count": len(document["review_queue"]),
        })
    return {
        "schema_version": ANALYSIS_BUNDLE_SCHEMA_VERSION,
        "documents": documents,
        "index": {
            "documents": document_index,
            "event_references": event_references,
        },
        "statistics": {
            "document_count": len(documents),
            "event_count": event_count,
            "indexed_event_count": indexed_event_count,
            "style_count": style_count,
            "review_count": review_count,
            "record_types": dict(sorted(record_types.items())),
            "event_id_statuses": dict(sorted(id_statuses.items())),
        },
    }


def load_analysis_bundle(value: Path | str | Mapping[str, Any]) -> dict[str, Any]:
    payload = _load_json_value(value, "analysis bundle")
    if payload.get("schema_version") != ANALYSIS_BUNDLE_SCHEMA_VERSION:
        raise ValueError("analysis bundle has an unexpected schema version")
    documents = payload.get("documents")
    if not isinstance(documents, list):
        raise ValueError("analysis bundle documents must be a list")
    rebuilt = combine_analyses(tuple(documents))
    if payload != rebuilt:
        raise ValueError("analysis bundle index or statistics are inconsistent")
    return deepcopy(payload)


def export_analysis_bundle(
    payload: Mapping[str, Any], target: Path | str, *, overwrite: bool = False,
    indent: int | None = 2,
) -> Path:
    validated = load_analysis_bundle(payload)
    text = json.dumps(validated, ensure_ascii=False, indent=indent) + "\n"
    return _atomic_export(text, target, overwrite=overwrite)


def index_bundle_events(
    bundle: Mapping[str, Any],
) -> dict[tuple[str, str], dict[str, Any]]:
    validated = load_analysis_bundle(bundle)
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for document in validated["documents"]:
        source_id = document["source"]["artifact_id"]
        for event_id, event in index_analysis_events(document).items():
            result[(source_id, event_id)] = event
    return result


def get_bundle_event(
    bundle_or_index: Mapping[Any, Any], source_artifact_id: str, event_id: str,
) -> dict[str, Any] | None:
    if "documents" in bundle_or_index:
        for document in bundle_or_index.get("documents", []):
            if document.get("source", {}).get("artifact_id") == source_artifact_id:
                return get_analysis_event(document, event_id)
        return None
    item = bundle_or_index.get((source_artifact_id, event_id))
    return deepcopy(dict(item)) if isinstance(item, Mapping) else None


def _validate_v4(
    payload: Mapping[str, Any], events: Mapping[str, Any], items: list[Any],
) -> set[str]:
    event_format = events.get("format")
    if not isinstance(event_format, list) or any(not isinstance(item, str) for item in event_format):
        raise ValueError("analysis Event Format is invalid")
    if events.get("id_strategy") != event_id_strategy():
        raise ValueError("analysis event ID strategy is invalid")
    if events.get("text_id_strategy") != text_id_strategy():
        raise ValueError("analysis text ID strategy is invalid")
    source_refs: set[str] = set()
    duplicate_ordinals: dict[str, list[int]] = {}
    for item in items:
        if not isinstance(item, Mapping):
            raise ValueError("analysis Event item must be an object")
        fields = item.get("fields")
        text = item.get("text")
        metadata = item.get("id")
        if not isinstance(fields, Mapping) or not isinstance(text, Mapping) or not isinstance(metadata, Mapping):
            raise ValueError("analysis Event fields, text, or ID metadata is invalid")
        event = AssEvent(
            line_number=_integer(item.get("line"), "line"),
            ordinal=_integer(item.get("ordinal"), "ordinal"),
            record_type=str(item.get("record_type", "")), raw="",
            fields={str(key): str(value) for key, value in fields.items()},
        )
        canonical, resolved_fields, _ = canonical_event_input(
            event, event_format=event_format, mode="all_fields_v2",
        )
        event_id = item.get("event_id")
        if not isinstance(event_id, str) or not _EVENT_ID_RE.fullmatch(event_id):
            raise ValueError("analysis Event ID does not match the declared strategy")
        if hash_canonical_input(canonical) != event_id:
            raise ValueError("analysis Event ID does not match its content")
        source_ref = item.get("source_ref")
        duplicate_ordinal = item.get("duplicate_ordinal")
        if (not isinstance(source_ref, str) or not _SOURCE_REF_RE.fullmatch(source_ref) or
                isinstance(duplicate_ordinal, bool) or not isinstance(duplicate_ordinal, int) or
                duplicate_ordinal < 0 or source_ref != f"{event_id}#{duplicate_ordinal}"):
            raise ValueError("analysis Event source reference is invalid")
        if source_ref in source_refs:
            raise ValueError("analysis Event source references must be unique")
        source_refs.add(source_ref)
        duplicate_ordinals.setdefault(event_id, []).append(duplicate_ordinal)
        text_id = item.get("text_id")
        plain = str(text.get("plain_text", ""))
        expected_text_id = (
            hash_canonical_input(canonical_text_input(plain), prefix="t_")
            if plain.strip() else None
        )
        if text_id != expected_text_id or (
            text_id is not None and (not isinstance(text_id, str) or not _TEXT_ID_RE.fullmatch(text_id))
        ):
            raise ValueError("analysis text ID does not match visible text")
        if metadata.get("event_id") != event_id or metadata.get("value") != event_id:
            raise ValueError("analysis Event ID metadata is inconsistent")
        if metadata.get("text_id") != text_id or metadata.get("source_ref") != source_ref:
            raise ValueError("analysis source/text ID metadata is inconsistent")
        if metadata.get("duplicate_ordinal") != duplicate_ordinal:
            raise ValueError("analysis duplicate ordinal metadata is inconsistent")
        if metadata.get("strategy") != EVENT_ID_STRATEGY_VERSION or metadata.get("logic_version") != EVENT_ID_INPUT_VERSION:
            raise ValueError("analysis Event ID version is invalid")
        if metadata.get("input_fields") != list(resolved_fields):
            raise ValueError("analysis Event ID input fields are invalid")
        text_metadata = metadata.get("text")
        if not isinstance(text_metadata, Mapping) or text_metadata.get("strategy") != TEXT_ID_STRATEGY_VERSION or text_metadata.get("logic_version") != TEXT_ID_INPUT_VERSION or text_metadata.get("value") != text_id:
            raise ValueError("analysis text ID metadata is invalid")
        _validate_v2_text(item)
    if any(sorted(values) != list(range(len(values))) for values in duplicate_ordinals.values()):
        raise ValueError("analysis duplicate ordinals must be contiguous per Event ID")
    semantic_groups = events.get("semantic_groups")
    if not isinstance(semantic_groups, list):
        raise ValueError("analysis semantic groups must be an array")
    grouped: set[str] = set()
    group_ids: set[str] = set()
    for group in semantic_groups:
        if not isinstance(group, Mapping) or not re.fullmatch(r"g_[0-9a-f]{16}", str(group.get("group_id", ""))):
            raise ValueError("analysis semantic group ID is invalid")
        if group["group_id"] in group_ids:
            raise ValueError("analysis semantic group IDs must be unique")
        group_ids.add(group["group_id"])
        refs = group.get("source_refs")
        _validate_event_references(refs, source_refs)
        if any(ref in grouped for ref in refs):
            raise ValueError("analysis source reference belongs to multiple semantic groups")
        grouped.update(refs)
        if group.get("parent_source_ref") is not None and group.get("parent_source_ref") not in refs:
            raise ValueError("analysis semantic group parent is not a group member")
    return source_refs


def _validate_v3(
    payload: Mapping[str, Any], events: Mapping[str, Any], items: list[Any],
) -> set[str]:
    event_format = events.get("format")
    if not isinstance(event_format, list) or any(not isinstance(item, str) for item in event_format):
        raise ValueError("analysis Event Format is invalid")
    strategy = events.get("id_strategy")
    if not isinstance(strategy, Mapping):
        raise ValueError("analysis event ID strategy is invalid")
    legacy_strategy_version = str(strategy.get("name", ""))
    legacy_logic_version = str(strategy.get("input_logic_version", ""))
    policy = EventIdPolicy(
        mode=str(strategy.get("default_mode", "")),
        fields=tuple(strategy.get("input_fields", ())),
        calculate_empty_text=strategy.get("empty_text") == "line_fields",
        duplicate_policy=str(strategy.get("duplicate_policy", "")),
    )
    expected_strategy = {
        "name": "ass-event-content-xxh3_64-v1", "scope": "document",
        "hash_algorithm": "xxh3_64", "seed": 0, "encoding": "utf-8",
        "prefix": "e_", "hex_length": 16,
        "input_logic_version": "ass-event-id-input-v1",
        "default_mode": policy.mode, "input_fields": list(policy.fields),
        "empty_text": "line_fields" if policy.calculate_empty_text else "skip",
        "duplicate_policy": policy.duplicate_policy,
    }
    if dict(strategy) != expected_strategy:
        raise ValueError("analysis event ID strategy is invalid")
    generated: set[str] = set()
    canonical_by_status: dict[str, list[bytes]] = {}
    for item in items:
        if not isinstance(item, Mapping):
            raise ValueError("analysis Event item must be an object")
        metadata = item.get("id")
        if not isinstance(metadata, Mapping):
            raise ValueError("analysis Event ID metadata is missing")
        event_id = item.get("event_id")
        if metadata.get("value") != event_id:
            raise ValueError("analysis Event ID value is inconsistent")
        if metadata.get("algorithm") != "xxh3_64":
            raise ValueError("analysis Event hash algorithm is invalid")
        if metadata.get("strategy") != legacy_strategy_version:
            raise ValueError("analysis Event ID strategy version is invalid")
        if metadata.get("logic_version") != legacy_logic_version:
            raise ValueError("analysis Event ID input logic is invalid")
        mode = metadata.get("mode")
        input_fields = metadata.get("input_fields")
        if mode not in {"visible_text", "raw_text", "fields", "line_fields"}:
            raise ValueError("analysis Event ID mode is invalid")
        if not isinstance(input_fields, list) or any(not isinstance(name, str) for name in input_fields):
            raise ValueError("analysis Event ID input fields are invalid")
        fields = item.get("fields")
        text = item.get("text")
        if not isinstance(fields, Mapping) or not isinstance(text, Mapping):
            raise ValueError("analysis Event fields or text are invalid")
        event = AssEvent(
            line_number=_integer(item.get("line"), "line"),
            ordinal=_integer(item.get("ordinal"), "ordinal"),
            record_type=str(item.get("record_type", "")),
            raw="", fields={str(key): str(value) for key, value in fields.items()},
        )
        canonical, resolved_fields, tags_removed = canonical_event_input(
            event, event_format=event_format, mode=mode,
            input_fields=input_fields, plain_text=str(text.get("plain_text", "")),
            logic_version=legacy_logic_version,
        )
        if list(resolved_fields) != input_fields:
            raise ValueError("analysis Event ID input field order is invalid")
        if metadata.get("tags_removed") is not tags_removed:
            raise ValueError("analysis Event ID tag-removal declaration is invalid")
        expected_fallback = mode == "line_fields"
        if metadata.get("duplicate_fallback") is not expected_fallback:
            raise ValueError("analysis Event ID fallback declaration is invalid")
        status = metadata.get("status")
        if status == "generated":
            if not isinstance(event_id, str) or not _EVENT_ID_RE.fullmatch(event_id):
                raise ValueError("analysis Event ID does not match the declared strategy")
            if hash_canonical_input(canonical) != event_id:
                raise ValueError("analysis Event ID does not match its content")
            if event_id in generated:
                raise ValueError("analysis generated Event IDs must be unique")
            generated.add(event_id)
        elif status in _NULL_ID_STATUSES:
            if event_id is not None:
                raise ValueError("analysis null Event ID status has a non-null value")
            if status == "skipped_empty_text":
                if mode != "visible_text" or text.get("plain_text") != "":
                    raise ValueError("analysis skipped empty-text Event is inconsistent")
            canonical_by_status.setdefault(status, []).append(canonical)
        else:
            raise ValueError("analysis Event ID status is invalid")
        _validate_v2_text(item)
    duplicate_canonicals = canonical_by_status.get("duplicate_identical_event", [])
    if any(duplicate_canonicals.count(value) < 2 for value in duplicate_canonicals):
        raise ValueError("analysis identical-Event status is not supported by duplicate content")
    return generated


def _validate_v2(
    payload: Mapping[str, Any], events: Mapping[str, Any], items: list[Any],
) -> set[str]:
    strategy = events.get("id_strategy")
    if strategy != {"name": "ass-event-ordinal-v1", "scope": "document"}:
        raise ValueError("legacy v2 Event ID strategy is invalid")
    event_ids = []
    for item in items:
        if not isinstance(item, Mapping):
            raise ValueError("analysis Event item must be an object")
        event_id = item.get("event_id")
        if not isinstance(event_id, str) or not _LEGACY_EVENT_ID_RE.fullmatch(event_id):
            raise ValueError("legacy v2 Event ID is invalid")
        event_ids.append(event_id)
        _validate_v2_text(item)
    if len(event_ids) != len(set(event_ids)):
        raise ValueError("legacy v2 Event IDs must be unique")
    return set(event_ids)


def _validate_v2_text(item: Mapping[str, Any]) -> None:
    text = item.get("text")
    if not isinstance(text, Mapping):
        raise ValueError("analysis event text must be an object")
    names = text.get("tag_names")
    blocks = text.get("blocks")
    override_blocks = text.get("override_blocks")
    if not isinstance(names, list) or any(
        not isinstance(name, str) or not name.startswith("\\") for name in names
    ):
        raise ValueError("analysis tag names must include the ASS backslash")
    if not isinstance(blocks, list) or not isinstance(override_blocks, list):
        raise ValueError("analysis text blocks are incomplete")
    flattened = []
    for block in override_blocks:
        if not isinstance(block, Mapping) or not isinstance(block.get("tags"), list):
            raise ValueError("analysis override block is invalid")
        flattened.extend(_validate_tags(block["tags"]))
    if flattened != names:
        raise ValueError("analysis tag names do not match the structured tags")


def _validate_common_references(
    payload: Mapping[str, Any], events: Mapping[str, Any], event_refs: set[str],
) -> None:
    for group in events.get("groups", []):
        if isinstance(group, Mapping):
            values = group.get("source_refs", group.get("event_ids", []))
            _validate_event_references(values, event_refs)
    for item in payload.get("review_queue", []):
        if isinstance(item, Mapping):
            value = item.get("source_ref", item.get("event_id"))
            if value is not None:
                _validate_event_references([value], event_refs)
    fonts = payload.get("fonts", {})
    if isinstance(fonts, Mapping):
        for reference in fonts.get("references", []):
            if isinstance(reference, Mapping):
                value = reference.get("source_ref", reference.get("event_id"))
                if value is not None:
                    _validate_event_references([value], event_refs)
        for requirement in fonts.get("requirements", []):
            if isinstance(requirement, Mapping):
                values = requirement.get("source_refs", requirement.get("event_ids", []))
                _validate_event_references(values, event_refs)


def _validate_tags(tags: list[Any]) -> list[str]:
    flattened = []
    for tag in tags:
        if not isinstance(tag, Mapping):
            raise ValueError("analysis override tag must be an object")
        name = tag.get("name")
        canonical = tag.get("canonical_name")
        if not isinstance(name, str) or not name.startswith("\\") or name[1:] != canonical:
            raise ValueError("analysis override tag name is inconsistent")
        if not isinstance(tag.get("argument"), str) or not isinstance(tag.get("raw"), str):
            raise ValueError("analysis override tag argument or raw value is invalid")
        nested = tag.get("nested")
        if not isinstance(nested, list):
            raise ValueError("analysis override tag nested value must be a list")
        flattened.append(name)
        flattened.extend(_validate_tags(nested))
    return flattened


def _validate_event_references(values: Any, event_ids: set[str]) -> None:
    if not isinstance(values, list) or any(value not in event_ids for value in values):
        raise ValueError("analysis contains an unknown Event reference")


def _integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"analysis Event {name} must be an integer")
    return value


def _load_json_value(value: Path | str | Mapping[str, Any], label: str) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return deepcopy(dict(value))
    target = Path(value)
    if not target.is_file() or target.stat().st_size <= 0:
        raise ValueError(f"{label} file is missing or empty")
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def _atomic_export(text: str, target: Path | str, *, overwrite: bool) -> Path:
    path = Path(target).expanduser().resolve()
    if path.exists() and not overwrite:
        raise FileExistsError(f"analysis export already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists() and not overwrite:
            raise FileExistsError(f"analysis export already exists: {path}")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return path
