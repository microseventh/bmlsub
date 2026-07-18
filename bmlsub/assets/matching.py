"""Deterministic candidate matching and explicit confirmation."""

from __future__ import annotations

from ..version import __version__

from pathlib import Path
import re
import uuid

from ..execution.errors import BmlsubError, ErrorCode, ReviewRequiredError
from ..execution.stage_runner import StageContext, StageOutcome, StageRunner
from ..state.fingerprints import artifact_matches, fingerprint_parameters, fingerprint_tools, hash_json
from ..state.models import (
    ArtifactRecord, AssetMatchCandidateRecord, AssetMatchSetRecord, AssetMatchStatus,
    Diagnostic, DiagnosticLevel, StageInputBinding, StageResult, StageStatus, utc_now,
)
from ..state.sqlite_store import SQLiteJobStore


MATCH_RULE_VERSION = "asset-match-v1"
ROLE_TYPES = {
    "subtitle": ("source.subtitle.ass", "source.subtitle.srt"),
    "font": ("source.font",),
    "chapter": ("source.chapter",),
    "attachment": ("source.attachment",),
}
_DECORATION_RE = re.compile(
    r"(?:\[[^\]]+\]|\([^)]*(?:720p|1080p|2160p|[0-9A-Fa-f]{8})[^)]*\)|"
    r"\b(?:720p|1080p|2160p|x264|x265|hevc|av1|webrip|bluray|chs|cht|jpn|eng)\b)",
    re.IGNORECASE,
)
_SEPARATOR_RE = re.compile(r"[^0-9a-z぀-ヿ㐀-鿿]+", re.IGNORECASE)


def refresh_artifact(store: SQLiteJobStore, artifact: ArtifactRecord) -> ArtifactRecord:
    if artifact.validation_status.value == "valid" and not artifact_matches(
        artifact, verify_hash=artifact.content_hash is not None
    ):
        return store.mark_artifact_stale(artifact.artifact_id)
    return artifact


def run_asset_matching(
    *, workspace: Path | str, episode_id: str, anchor_artifact_id: str,
    roles: tuple[str, ...] = ("subtitle", "font", "chapter", "attachment"),
    store: SQLiteJobStore | None = None, state_dir: Path | str | None = None,
    force: bool = False, replace_confirmed: bool = False,
) -> StageResult:
    root = Path(workspace).expanduser().resolve()
    ledger = store or SQLiteJobStore.for_workspace(root, state_dir)
    ledger.initialize()
    anchor = ledger.get_artifact(anchor_artifact_id)
    if anchor is None or anchor.episode_id != episode_id or anchor.artifact_type not in {"source.video", "reference.video"}:
        raise BmlsubError("matching anchor must be a video in the requested episode", code=ErrorCode.INPUT_MISSING)
    anchor = refresh_artifact(ledger, anchor)
    if anchor.validation_status.value != "valid":
        raise BmlsubError("matching anchor is stale", code=ErrorCode.INPUT_MISSING)
    normalized_roles = tuple(dict.fromkeys(role.strip() for role in roles))
    if not normalized_roles or any(role not in ROLE_TYPES for role in normalized_roles):
        raise ValueError("matching roles must be subtitle, font, chapter, or attachment")
    assets = [
        refresh_artifact(ledger, item) for item in ledger.list_artifacts(episode_id=episode_id)
        if item.artifact_id != anchor.artifact_id
    ]
    assets = [item for item in assets if item.validation_status.value == "valid"]
    evaluated = [item for item in assets if any(item.artifact_type in ROLE_TYPES[role] for role in normalized_roles)]
    input_fp = hash_json([
        {"artifact_id": item.artifact_id, "fingerprint": item.source_fingerprint}
        for item in (anchor, *sorted(evaluated, key=lambda value: value.artifact_id))
    ])
    parameter_fp = fingerprint_parameters({
        "roles": normalized_roles, "rule_version": MATCH_RULE_VERSION,
        "replace_confirmed": replace_confirmed,
    })
    tool_fp = fingerprint_tools({"bmlsub": __version__, "matcher": MATCH_RULE_VERSION})

    def adapter(context: StageContext) -> StageOutcome:
        match_sets = []
        review_roles = []
        for role in normalized_roles:
            candidates = [item for item in evaluated if item.artifact_type in ROLE_TYPES[role]]
            referenced_fonts = {
                str(font).strip().lower()
                for item in evaluated if item.artifact_type == "source.subtitle.ass"
                for font in item.metadata.get("referenced_fonts", [])
                if str(font).strip()
            }
            ranked = _rank_candidates(anchor, candidates, role, referenced_fonts)
            status = _status_for(ranked)
            if status in {AssetMatchStatus.AMBIGUOUS, AssetMatchStatus.UNMATCHED}:
                review_roles.append(role)
            match_set_id = uuid.uuid4().hex
            record = AssetMatchSetRecord(
                match_set_id=match_set_id, stage_id=context.stage_id,
                episode_id=episode_id, anchor_artifact_id=anchor.artifact_id,
                input_role=role, status=status, rule_version=MATCH_RULE_VERSION,
                created_at=utc_now(),
                candidates=tuple(AssetMatchCandidateRecord(
                    match_set_id, item.artifact_id, rank, score, evidence
                ) for rank, (item, score, evidence) in enumerate(ranked)),
            )
            match_sets.append(ledger.register_match_set(record, replace_confirmed=replace_confirmed))
        diagnostics = [Diagnostic(
            code="asset_match_candidates_created",
            message="candidate asset relationships were evaluated without automatic confirmation",
            context={"roles": list(normalized_roles)},
        )]
        if review_roles:
            diagnostics.append(Diagnostic(
                code="asset_match_review_required",
                message="one or more asset roles are ambiguous or unmatched",
                level=DiagnosticLevel.WARNING,
                context={"roles": review_roles},
            ))
        return StageOutcome(
            status=StageStatus.NEEDS_REVIEW if review_roles else StageStatus.SUCCEEDED,
            diagnostics=tuple(diagnostics),
        )

    bindings = [StageInputBinding(anchor.artifact_id, "video", 0)]
    bindings.extend(
        StageInputBinding(item.artifact_id, "candidate", ordinal)
        for ordinal, item in enumerate(sorted(evaluated, key=lambda value: value.artifact_id))
    )
    return StageRunner(ledger).run(
        workspace=root, command_name="asset.match", stage_name="asset.match",
        episode_id=episode_id, input_fingerprint=input_fp,
        parameter_fingerprint=parameter_fp, tool_fingerprint=tool_fp,
        adapter=adapter, inputs=bindings, force=force,
    )


def run_match_confirmation(
    *, workspace: Path | str, episode_id: str, anchor_artifact_id: str,
    role: str, artifact_ids: tuple[str, ...], store: SQLiteJobStore | None = None,
    state_dir: Path | str | None = None, force: bool = False,
) -> StageResult:
    root = Path(workspace).expanduser().resolve()
    ledger = store or SQLiteJobStore.for_workspace(root, state_dir)
    ledger.initialize()
    match_set = ledger.get_current_match_set(episode_id, anchor_artifact_id, role)
    if match_set is None:
        raise ReviewRequiredError("no current match set exists for this anchor and role")
    artifacts = [ledger.get_artifact(item) for item in artifact_ids]
    if any(item is None for item in artifacts):
        raise BmlsubError("selected artifact was not found", code=ErrorCode.INPUT_MISSING)
    concrete = [refresh_artifact(ledger, item) for item in artifacts if item is not None]
    input_fp = hash_json([anchor_artifact_id, role, *artifact_ids])
    parameter_fp = fingerprint_parameters({"match_set_id": match_set.match_set_id, "role": role})
    tool_fp = fingerprint_tools({"bmlsub": __version__, "confirmation": "match-confirm-v1"})

    def adapter(context: StageContext) -> StageOutcome:
        confirmed = ledger.confirm_match_set(match_set.match_set_id, context.stage_id, artifact_ids)
        return StageOutcome(diagnostics=(Diagnostic(
            code="asset_match_confirmed", message="explicit asset selection was confirmed",
            context={"match_set_id": confirmed.match_set_id, "artifact_count": len(artifact_ids)},
        ),))

    bindings = [StageInputBinding(anchor_artifact_id, "video", 0)]
    bindings.extend(StageInputBinding(item.artifact_id, role, ordinal) for ordinal, item in enumerate(concrete))
    return StageRunner(ledger).run(
        workspace=root, command_name="asset.confirm", stage_name=f"asset.confirm_{role}",
        episode_id=episode_id, input_fingerprint=input_fp,
        parameter_fingerprint=parameter_fp, tool_fingerprint=tool_fp,
        adapter=adapter, inputs=bindings, force=force,
    )


def episode_manifest(store: SQLiteJobStore, episode_id: str) -> dict[str, object]:
    artifacts = []
    for item in store.list_artifacts(episode_id=episode_id):
        refreshed = refresh_artifact(store, item)
        if refreshed.validation_status.value == "valid":
            artifacts.append(refreshed.to_dict())
    matches = [item.to_dict() for item in store.list_current_match_sets(episode_id)]
    current_ids = {item["artifact_id"] for item in artifacts}
    needs_review = any(
        item["status"] in {AssetMatchStatus.AMBIGUOUS.value, AssetMatchStatus.UNMATCHED.value}
        or item["anchor_artifact_id"] not in current_ids
        or (item["status"] == AssetMatchStatus.CONFIRMED.value and (
            not item["selections"]
            or any(selection["artifact_id"] not in current_ids for selection in item["selections"])
        ))
        for item in matches
    )
    return {
        "status": "needs_review" if needs_review else "succeeded",
        "needs_review": needs_review, "episode_id": episode_id,
        "artifacts": artifacts, "matches": matches,
    }


def _rank_candidates(anchor: ArtifactRecord, candidates: list[ArtifactRecord],
                     role: str, referenced_fonts: set[str] | None = None
                     ) -> list[tuple[ArtifactRecord, int, dict[str, object]]]:
    ranked = []
    anchor_stem = _normalized_stem(anchor.path)
    anchor_duration = ((anchor.metadata.get("media") or {}).get("duration_ms")
                       if isinstance(anchor.metadata.get("media"), dict) else None)
    for candidate in candidates:
        score = 20
        evidence: dict[str, object] = {"same_episode": True}
        candidate_stem = _normalized_stem(candidate.path)
        if candidate_stem == anchor_stem:
            score += 55
            evidence["stem"] = "equal"
        elif candidate_stem and (candidate_stem in anchor_stem or anchor_stem in candidate_stem):
            score += 35
            evidence["stem"] = "contained"
        if candidate.path.parent == anchor.path.parent:
            score += 15
            evidence["same_directory"] = True
        duration = candidate.metadata.get("duration_ms")
        if isinstance(anchor_duration, int) and isinstance(duration, int):
            delta = abs(anchor_duration - duration)
            tolerance = max(3000, round(anchor_duration * 0.02))
            compatible = delta <= tolerance
            evidence["duration_delta_ms"] = delta
            evidence["duration_compatible"] = compatible
            score += 20 if compatible else -25
        if role == "font":
            hint = str(candidate.metadata.get("filename_family_hint", "")).lower()
            normalized_fonts = referenced_fonts or set()
            if hint and any(hint == font or hint in font or font in hint for font in normalized_fonts):
                score += 45
                evidence["ass_font_reference"] = True
        ranked.append((candidate, score, evidence))
    return sorted(ranked, key=lambda item: (-item[1], item[0].artifact_id))


def _status_for(ranked: list[tuple[ArtifactRecord, int, dict[str, object]]]) -> AssetMatchStatus:
    plausible = [item for item in ranked if item[1] >= 55]
    if not plausible:
        return AssetMatchStatus.UNMATCHED
    if len(plausible) == 1 or plausible[0][1] > plausible[1][1]:
        return AssetMatchStatus.INFERRED
    return AssetMatchStatus.AMBIGUOUS


def _normalized_stem(path: Path) -> str:
    value = _DECORATION_RE.sub(" ", path.stem.lower())
    return _SEPARATOR_RE.sub("", value)
