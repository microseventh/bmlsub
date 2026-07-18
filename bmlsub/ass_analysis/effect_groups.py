"""Conservative semantic grouping for expanded ASS song effects."""

from __future__ import annotations

from collections import Counter, defaultdict
import json
from typing import Any, Mapping

import xxhash

from .constants import EFFECT_GROUPER_VERSION
from .profiles import EffectCollapsePolicy

_SONG_ROLES = {"op", "ed", "insert_song"}


def build_effect_groups(
    items: list[dict[str, Any]], policy: EffectCollapsePolicy,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return deterministic semantic groups and review items without mutating Events."""
    allowed_roles = set(policy.roles) & _SONG_ROLES
    parents = [item for item in items if _is_parent(item, allowed_roles)]
    children = [item for item in items if _is_expanded_dialogue(item, allowed_roles)]
    used: set[str] = set()
    groups: list[dict[str, Any]] = []
    review: list[dict[str, Any]] = []

    for parent in sorted(parents, key=_event_sort_key):
        parent_ref = parent["source_ref"]
        matches = [
            child for child in children
            if child["source_ref"] not in used and _matches_parent(
                parent, child, policy.parent_time_tolerance_ms,
            )
        ]
        if len(matches) < policy.minimum_expanded_events:
            review.append({
                "kind": "semantic_group", "source_ref": parent_ref,
                "reason": "effect_parent_without_expansion",
                "candidate_count": len(matches),
            })
            continue
        source_refs = [parent_ref, *(item["source_ref"] for item in sorted(matches, key=_event_sort_key))]
        group = _group(
            parent=parent, members=matches, source_refs=source_refs,
            method="templater_parent", confidence=1.0,
            signals=("karaoke-parent", "expanded-dialogue", "matching-visible-text"),
        )
        groups.append(group)
        used.update(source_refs)

    remaining: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for child in children:
        if child["source_ref"] in used or not child.get("text_id"):
            continue
        classification = child["classification"]
        remaining[(
            classification["content_role"], classification["language"],
            _style_family(child), child["text_id"],
        )].append(child)
    for candidates in remaining.values():
        for cluster in _time_clusters(candidates, policy.cluster_gap_ms):
            if len(cluster) < policy.minimum_expanded_events:
                continue
            times = Counter((item.get("start_ms"), item.get("end_ms")) for item in cluster)
            (start_ms, end_ms), count = sorted(
                times.items(), key=lambda pair: (-pair[1], pair[0]),
            )[0]
            if count / len(cluster) < policy.dominant_time_ratio:
                review.extend({
                    "kind": "semantic_group", "source_ref": item["source_ref"],
                    "reason": "unstable_effect_time_cluster",
                } for item in cluster)
                continue
            source_refs = [item["source_ref"] for item in sorted(cluster, key=_event_sort_key)]
            representative = min(cluster, key=_event_sort_key)
            group = _group(
                parent={**representative, "start_ms": start_ms, "end_ms": end_ms},
                members=cluster, source_refs=source_refs,
                method="repeated_full_text", confidence=count / len(cluster),
                signals=("repeated-visible-text", "dominant-time-pair", "expanded-dialogue"),
            )
            groups.append(group)
            used.update(source_refs)

    groups.sort(key=lambda item: (
        item["start_ms"], item["end_ms"], item["role"], item["language"], item["group_id"],
    ))
    return groups, review


def effect_expansion_evidence(item: Mapping[str, Any]) -> dict[str, Any]:
    classification = item.get("classification", {})
    features = item.get("text", {})
    effect = str(item.get("fields", {}).get("effect", "")).strip().casefold()
    signals = []
    if classification.get("event_kind") == "templater_control":
        signals.append("templater-control")
    if classification.get("event_kind") == "karaoke_parent":
        signals.append("karaoke-parent")
    if effect in {"fx", "karaoke"}:
        signals.append(f"effect:{effect}")
    for name, signal in (
        ("has_position", "position"), ("has_animation", "animation"),
        ("has_clip", "clip"), ("has_karaoke", "karaoke-tags"),
    ):
        if features.get(name):
            signals.append(signal)
    is_candidate = (
        classification.get("content_role") in _SONG_ROLES and
        (classification.get("event_kind") in {"karaoke_parent", "templater_control"} or
         item.get("record_type") == "dialogue" and len(signals) >= 2)
    )
    return {
        "is_candidate": is_candidate,
        "score": min(1.0, len(signals) / 4),
        "signals": signals,
        "grouper_version": EFFECT_GROUPER_VERSION,
    }


def _is_parent(item: Mapping[str, Any], roles: set[str]) -> bool:
    classification = item.get("classification", {})
    return (
        classification.get("event_kind") == "karaoke_parent" and
        classification.get("content_role") in roles and
        isinstance(item.get("start_ms"), int) and isinstance(item.get("end_ms"), int) and
        bool(item.get("text_id"))
    )


def _is_expanded_dialogue(item: Mapping[str, Any], roles: set[str]) -> bool:
    classification = item.get("classification", {})
    evidence = item.get("effect_expansion", {})
    return (
        item.get("record_type") == "dialogue" and
        classification.get("content_role") in roles and
        evidence.get("is_candidate") is True and
        isinstance(item.get("start_ms"), int) and isinstance(item.get("end_ms"), int) and
        bool(item.get("text", {}).get("plain_text"))
    )


def _matches_parent(parent: Mapping[str, Any], child: Mapping[str, Any], tolerance: int) -> bool:
    parent_class = parent["classification"]
    child_class = child["classification"]
    if parent_class["content_role"] != child_class["content_role"]:
        return False
    if parent_class["language"] != child_class["language"]:
        return False
    if _style_family(parent) != _style_family(child):
        return False
    if parent.get("text_id") != child.get("text_id"):
        return False
    return (
        child["end_ms"] >= parent["start_ms"] - tolerance and
        child["start_ms"] <= parent["end_ms"] + tolerance
    )


def _time_clusters(items: list[dict[str, Any]], gap_ms: int) -> list[list[dict[str, Any]]]:
    ordered = sorted(items, key=_event_sort_key)
    clusters: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_end = -1
    for item in ordered:
        if current and item["start_ms"] > current_end + gap_ms:
            clusters.append(current)
            current = []
        current.append(item)
        current_end = max(current_end, item["end_ms"])
    if current:
        clusters.append(current)
    return clusters


def _group(*, parent: Mapping[str, Any], members: list[dict[str, Any]],
           source_refs: list[str], method: str, confidence: float,
           signals: tuple[str, ...]) -> dict[str, Any]:
    classification = parent["classification"]
    identity = json.dumps({
        "method": method, "role": classification["content_role"],
        "language": classification["language"], "text_id": parent.get("text_id"),
        "start_ms": parent["start_ms"], "end_ms": parent["end_ms"],
        "source_refs": source_refs,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "group_id": "g_" + xxhash.xxh3_64_hexdigest(identity, seed=0),
        "role": classification["content_role"],
        "language": classification["language"],
        "style_family": _style_family(parent),
        "text_id": parent.get("text_id"),
        "text": parent.get("text", {}).get("plain_text", ""),
        "start_ms": parent["start_ms"], "end_ms": parent["end_ms"],
        "parent_source_ref": parent.get("source_ref") if method == "templater_parent" else None,
        "source_refs": source_refs,
        "collapse_method": method, "confidence": confidence,
        "signals": list(signals), "review_required": False,
        "grouper_version": EFFECT_GROUPER_VERSION,
    }


def _style_family(item: Mapping[str, Any]) -> str:
    return str(item.get("style", "")).strip().casefold()


def _event_sort_key(item: Mapping[str, Any]):
    return (
        item.get("start_ms") if isinstance(item.get("start_ms"), int) else -1,
        item.get("end_ms") if isinstance(item.get("end_ms"), int) else -1,
        int(item.get("ordinal", 0)), str(item.get("source_ref", "")),
    )
