"""StageRunner integration for ASS analysis and controlled normalization."""

from __future__ import annotations

from ..version import __version__

from dataclasses import replace
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any, Iterable

from ..artifacts import ArtifactBatchWriter, ArtifactWriteSpec, ArtifactWriter
from ..execution.errors import BmlsubError, ErrorCode, ReviewRequiredError
from ..execution.stage_runner import StageContext, StageOutcome, StageRunner
from ..media.video import get_current_artifact
from ..state.fingerprints import fingerprint_parameters, fingerprint_tools, hash_json
from ..state.models import (
    ArtifactRecord, Diagnostic, DiagnosticLevel, StageInputBinding, StageResult,
    ValidationStatus,
)
from ..state.sqlite_store import SQLiteJobStore
from .analyzer import build_analysis
from .constants import (
    ANALYSIS_NAMING_VERSION, ANALYSIS_SCHEMA_VERSION, ANALYSIS_VALIDATOR_VERSION,
    ASS_SUBTITLE_TYPES, CLASSIFIER_VERSION, EFFECT_GROUPER_VERSION,
    EVENT_ID_INPUT_VERSION, EVENT_ID_STRATEGY_VERSION,
    TEXT_ID_INPUT_VERSION, TEXT_ID_STRATEGY_VERSION,
    FONT_ARTIFACT_TYPES, FONT_RESOLVER_VERSION,
    NORMALIZED_NAMING_VERSION, NORMALIZED_VALIDATOR_VERSION, PARSER_VERSION,
    RECONSTRUCTED_NAMING_VERSION, RECONSTRUCTED_VALIDATOR_VERSION,
    RECONSTRUCTOR_VERSION, SERIALIZER_VERSION, TAG_PARSER_VERSION, VIDEO_ARTIFACT_TYPES,
)
from .io import load_analysis, serialize_analysis
from .parser import read_ass_document
from .profiles import AssAnalysisProfile, AssReconstructionProfile
from .reconstruction import encode_reconstructed, reconstruct_standard_ass
from .serializer import encode_normalized, serialize_normalized
from .validators import (
    validate_analysis_file, validate_normalized_ass, validate_reconstructed_ass,
)


ANALYZE_ASS_STAGE = "subtitle.analyze_ass"
NORMALIZE_ASS_STAGE = "subtitle.normalize_ass"
RECONSTRUCT_ASS_STAGE = "subtitle.reconstruct_ass"


def run_ass_analysis(*, workspace: Path | str, episode_id: str,
                     subtitle_artifact_id: str,
                     video_artifact_id: str | None = None,
                     font_artifact_ids: Iterable[str] = (),
                     profile: AssAnalysisProfile | dict[str, Any] | None = None,
                     output: Path | str | None = None,
                     store: SQLiteJobStore | None = None,
                     state_dir: Path | str | None = None,
                     force: bool = False) -> StageResult:
    root, ledger, subtitle, video, fonts, normalized_profile = _prepare(
        workspace, episode_id, subtitle_artifact_id, video_artifact_id,
        tuple(font_artifact_ids), profile, store, state_dir,
    )
    target = _target(root, output, root / "outputs" / episode_id / "subtitle-analysis" /
                     f"{subtitle.path.stem}.analysis.json")
    input_fp, parameter_fp, tool_fp = _fingerprints(
        subtitle, video, fonts, normalized_profile, normalize=False,
    )

    def adapter(context: StageContext) -> StageOutcome:
        document = read_ass_document(subtitle.path)
        analysis = build_analysis(
            document, source_artifact=subtitle, profile=normalized_profile,
            font_artifacts=fonts, video_artifact=video,
        )
        if analysis["events"]["statistics"]["event_id_statuses"].get("hash_collision", 0):
            raise ReviewRequiredError("ASS Event ID hash collision requires human review")
        writer = ArtifactWriter(
            target, workspace=root, run_id=context.run_id, stage_id=context.stage_id,
            episode_id=episode_id, artifact_type="generated.subtitle.analysis.ass",
            source_fingerprint=input_fp, parameter_fingerprint=parameter_fp,
            metadata=_bounded_metadata(analysis),
        )
        result = writer.write(
            lambda path: path.write_text(
                serialize_analysis(analysis), encoding="utf-8",
            ),
            lambda path: validate_analysis_file(path, source_artifact_id=subtitle.artifact_id),
        )
        diagnostics = _result_diagnostics(
            result.backup_path, analysis, "ASS source was analyzed into a versioned JSON artifact",
        )
        return StageOutcome(artifacts=(result.artifact,), diagnostics=diagnostics)

    return StageRunner(ledger).run(
        workspace=root, command_name="subtitle.analyze-ass", stage_name=ANALYZE_ASS_STAGE,
        episode_id=episode_id, input_fingerprint=input_fp,
        parameter_fingerprint=parameter_fp, tool_fingerprint=tool_fp,
        adapter=adapter, inputs=_bindings(subtitle, video, fonts),
        run_metadata={"subtitle_artifact_id": subtitle.artifact_id,
                      "analysis_schema": ANALYSIS_SCHEMA_VERSION}, force=force,
    )


def run_ass_normalization(*, workspace: Path | str, episode_id: str,
                          subtitle_artifact_id: str,
                          video_artifact_id: str | None = None,
                          font_artifact_ids: Iterable[str] = (),
                          profile: AssAnalysisProfile | dict[str, Any] | None = None,
                          output: Path | str | None = None,
                          analysis_output: Path | str | None = None,
                          store: SQLiteJobStore | None = None,
                          state_dir: Path | str | None = None,
                          force: bool = False) -> StageResult:
    root, ledger, subtitle, video, fonts, normalized_profile = _prepare(
        workspace, episode_id, subtitle_artifact_id, video_artifact_id,
        tuple(font_artifact_ids), profile, store, state_dir,
    )
    normalized_target = _target(
        root, output, root / "outputs" / episode_id / "subtitles" /
        f"{subtitle.path.stem}.normalized.ass",
    )
    analysis_target = _target(
        root, analysis_output, root / "outputs" / episode_id / "subtitle-analysis" /
        f"{subtitle.path.stem}.normalized.analysis.json",
    )
    input_fp, parameter_fp, tool_fp = _fingerprints(
        subtitle, video, fonts, normalized_profile, normalize=True,
    )

    def adapter(context: StageContext) -> StageOutcome:
        document = read_ass_document(subtitle.path)
        if not document.roundtrip_safe:
            raise ReviewRequiredError("ASS structure is not safe for controlled normalization")
        if normalized_profile.metadata.require_confirmation:
            raise ReviewRequiredError("metadata policy requires human confirmation")
        serialization = serialize_normalized(document, normalized_profile)
        analysis = build_analysis(
            document, source_artifact=subtitle, profile=normalized_profile,
            font_artifacts=fonts, video_artifact=video,
        )
        if analysis["events"]["statistics"]["event_id_statuses"].get("hash_collision", 0):
            raise ReviewRequiredError("ASS Event ID hash collision requires human review")
        analysis["normalization"] = {"changes": list(serialization.changes)}
        review_decisions = [item for item in analysis["project_garbage"]["decisions"]
                            if item["action"] == "review"]
        if review_decisions:
            raise ReviewRequiredError(
                "Project Garbage cleanup policy contains unresolved review decisions",
                details={"review_field_count": len(review_decisions)},
            )
        validation: dict[str, Any] = {}

        def validate_ass(path: Path) -> None:
            candidate = validate_normalized_ass(path, source=document, profile=normalized_profile)
            validation.update({"roundtrip_safe": candidate.roundtrip_safe,
                               "event_count": len(candidate.events),
                               "style_count": len(candidate.styles)})

        specs = (
            ArtifactWriteSpec(
                analysis_target, "generated.subtitle.analysis.ass",
                lambda path: validate_analysis_file(path, source_artifact_id=subtitle.artifact_id),
                _bounded_metadata(analysis),
            ),
            ArtifactWriteSpec(
                normalized_target, "generated.subtitle.ass.normalized", validate_ass,
                {
                    "source_subtitle_artifact_id": subtitle.artifact_id,
                    "serializer_version": SERIALIZER_VERSION,
                    "validator_version": NORMALIZED_VALIDATOR_VERSION,
                    "change_count": len(serialization.changes),
                },
            ),
        )
        writer = ArtifactBatchWriter(
            workspace=root, run_id=context.run_id, stage_id=context.stage_id,
            episode_id=episode_id, source_fingerprint=input_fp,
            parameter_fingerprint=parameter_fp,
        )

        def produce(paths: tuple[Path, ...]) -> None:
            paths[0].write_text(serialize_analysis(analysis), encoding="utf-8")
            paths[1].write_bytes(encode_normalized(serialization, document))

        results = writer.write(specs, produce)
        artifacts = (results[0].artifact, replace(
            results[1].artifact,
            metadata={**dict(results[1].artifact.metadata), "validation": validation},
        ))
        diagnostics: list[Diagnostic] = []
        for result in results:
            if result.backup_path:
                diagnostics.append(Diagnostic(
                    code="artifact_backup_created", message="existing ASS output was backed up",
                    context={"path": str(result.backup_path)},
                ))
        diagnostics.extend(_result_diagnostics(
            None, analysis, "ASS source was normalized under an explicit policy",
        ))
        return StageOutcome(artifacts=artifacts, diagnostics=tuple(diagnostics))

    return StageRunner(ledger).run(
        workspace=root, command_name="subtitle.normalize-ass", stage_name=NORMALIZE_ASS_STAGE,
        episode_id=episode_id, input_fingerprint=input_fp,
        parameter_fingerprint=parameter_fp, tool_fingerprint=tool_fp,
        adapter=adapter, inputs=_bindings(subtitle, video, fonts),
        run_metadata={"subtitle_artifact_id": subtitle.artifact_id,
                      "analysis_schema": ANALYSIS_SCHEMA_VERSION}, force=force,
    )


def run_ass_reconstruction(*, workspace: Path | str, episode_id: str,
                           analysis_artifact_id: str,
                           profile: AssReconstructionProfile | dict[str, Any] | None = None,
                           output: Path | str | None = None,
                           store: SQLiteJobStore | None = None,
                           state_dir: Path | str | None = None,
                           force: bool = False) -> StageResult:
    root = Path(workspace).expanduser().resolve()
    if not episode_id.strip():
        raise ValueError("episode_id must not be empty")
    ledger = store or SQLiteJobStore.for_workspace(root, state_dir)
    ledger.initialize()
    analysis_artifact = _resolve(
        ledger, analysis_artifact_id, episode_id,
        {"generated.subtitle.analysis.ass"}, "analysis",
    )
    normalized_profile = AssReconstructionProfile.from_value(profile)
    target = _target(
        root, output, root / "outputs" / episode_id / "subtitles" /
        f"{analysis_artifact.path.stem.removesuffix('.analysis')}.standard.ass",
    )
    input_fp = hash_json([{
        "role": "analysis", "ordinal": 0,
        "artifact_id": analysis_artifact.artifact_id,
        "fingerprint": analysis_artifact.content_hash or analysis_artifact.source_fingerprint,
    }])
    parameter_fp = fingerprint_parameters({
        "analysis_schema": ANALYSIS_SCHEMA_VERSION,
        "profile": normalized_profile.to_dict(),
        "naming": RECONSTRUCTED_NAMING_VERSION,
    })
    tool_fp = fingerprint_tools({
        "bmlsub": __version__, "analysis_validator": ANALYSIS_VALIDATOR_VERSION,
        "reconstructor": RECONSTRUCTOR_VERSION, "parser": PARSER_VERSION,
        "validator": RECONSTRUCTED_VALIDATOR_VERSION,
    })

    def adapter(context: StageContext) -> StageOutcome:
        payload = load_analysis(analysis_artifact.path, allow_legacy=False)
        reconstruction = reconstruct_standard_ass(payload, normalized_profile)
        if reconstruction.review:
            raise ReviewRequiredError(
                "ASS analysis contains events that cannot be safely reconstructed",
                details={"review_event_count": len(reconstruction.review)},
            )
        writer = ArtifactWriter(
            target, workspace=root, run_id=context.run_id, stage_id=context.stage_id,
            episode_id=episode_id, artifact_type="generated.subtitle.ass.standard",
            source_fingerprint=input_fp, parameter_fingerprint=parameter_fp,
            metadata={
                "source_analysis_artifact_id": analysis_artifact.artifact_id,
                "source_subtitle_artifact_id": reconstruction.source_artifact_id,
                "reconstructor_version": RECONSTRUCTOR_VERSION,
                "validator_version": RECONSTRUCTED_VALIDATOR_VERSION,
                **dict(reconstruction.statistics),
            },
        )
        result = writer.write(
            lambda path: path.write_bytes(encode_reconstructed(reconstruction)),
            lambda path: validate_reconstructed_ass(path, result=reconstruction),
        )
        diagnostics: list[Diagnostic] = []
        if result.backup_path:
            diagnostics.append(Diagnostic(
                code="artifact_backup_created", message="existing standard ASS was backed up",
                context={"path": str(result.backup_path)},
            ))
        diagnostics.append(Diagnostic(
            code="ass_reconstructed",
            message="ASS analysis JSON was reconstructed into a standard subtitle",
            context={
                "output_event_count": reconstruction.statistics["output_event_count"],
                "consumed_source_count": reconstruction.statistics["consumed_source_count"],
                "semantic_group_count": reconstruction.statistics["semantic_group_count"],
                "skipped_event_count": reconstruction.statistics["skipped_event_count"],
            },
        ))
        if reconstruction.skipped:
            diagnostics.append(Diagnostic(
                code="ass_reconstruction_skipped_items",
                message="non-visible or drawing Events were excluded from the standard subtitle",
                level=DiagnosticLevel.WARNING,
                context={"skipped_event_count": len(reconstruction.skipped)},
            ))
        return StageOutcome(artifacts=(result.artifact,), diagnostics=tuple(diagnostics))

    return StageRunner(ledger).run(
        workspace=root, command_name="subtitle.reconstruct-ass",
        stage_name=RECONSTRUCT_ASS_STAGE, episode_id=episode_id,
        input_fingerprint=input_fp, parameter_fingerprint=parameter_fp,
        tool_fingerprint=tool_fp, adapter=adapter,
        inputs=(StageInputBinding(analysis_artifact.artifact_id, "analysis", 0),),
        run_metadata={
            "analysis_artifact_id": analysis_artifact.artifact_id,
            "analysis_schema": ANALYSIS_SCHEMA_VERSION,
        }, force=force,
    )


def _prepare(workspace, episode_id, subtitle_id, video_id, font_ids, profile, store, state_dir):
    root = Path(workspace).expanduser().resolve()
    if not episode_id.strip():
        raise ValueError("episode_id must not be empty")
    ledger = store or SQLiteJobStore.for_workspace(root, state_dir)
    ledger.initialize()
    subtitle = _resolve(ledger, subtitle_id, episode_id, ASS_SUBTITLE_TYPES, "subtitle")
    video = _resolve(ledger, video_id, episode_id, VIDEO_ARTIFACT_TYPES, "video") if video_id else None
    fonts = tuple(_resolve(ledger, item, episode_id, FONT_ARTIFACT_TYPES, "font")
                  for item in font_ids)
    return root, ledger, subtitle, video, fonts, AssAnalysisProfile.from_value(profile)


def _resolve(ledger, artifact_id, episode_id, accepted, role):
    artifact = get_current_artifact(ledger, artifact_id)
    if (artifact is None or artifact.validation_status is not ValidationStatus.VALID or
            artifact.episode_id != episode_id or artifact.artifact_type not in accepted):
        raise BmlsubError(f"ASS analysis {role} artifact is not current",
                          code=ErrorCode.INPUT_MISSING)
    return artifact


def _fingerprints(subtitle, video, fonts, profile, *, normalize):
    ordered = [("subtitle", 0, subtitle)]
    if video:
        ordered.append(("video", 0, video))
    ordered.extend(("font", index, item) for index, item in enumerate(fonts))
    input_fp = hash_json([{
        "role": role, "ordinal": ordinal, "artifact_id": item.artifact_id,
        "fingerprint": item.content_hash or item.source_fingerprint,
    } for role, ordinal, item in ordered])
    parameter_fp = fingerprint_parameters({
        "analysis_schema": ANALYSIS_SCHEMA_VERSION, "profile": profile.to_dict(),
        "normalize": normalize,
        "naming": NORMALIZED_NAMING_VERSION if normalize else ANALYSIS_NAMING_VERSION,
    })
    tool_fp = fingerprint_tools({
        "bmlsub": __version__, "parser": PARSER_VERSION, "tag_parser": TAG_PARSER_VERSION,
        "event_id_strategy": EVENT_ID_STRATEGY_VERSION,
        "event_id_input_logic": EVENT_ID_INPUT_VERSION,
        "text_id_strategy": TEXT_ID_STRATEGY_VERSION,
        "text_id_input_logic": TEXT_ID_INPUT_VERSION,
        "effect_grouper": EFFECT_GROUPER_VERSION,
        "xxhash": package_version("xxhash"),
        "classifier": CLASSIFIER_VERSION, "font_resolver": FONT_RESOLVER_VERSION,
        "serializer": SERIALIZER_VERSION if normalize else None,
        "validator": NORMALIZED_VALIDATOR_VERSION if normalize else ANALYSIS_VALIDATOR_VERSION,
    })
    return input_fp, parameter_fp, tool_fp


def _bindings(subtitle, video, fonts):
    result = [StageInputBinding(subtitle.artifact_id, "subtitle", 0)]
    if video:
        result.append(StageInputBinding(video.artifact_id, "video", 0))
    result.extend(StageInputBinding(item.artifact_id, "font", index)
                  for index, item in enumerate(fonts))
    return tuple(result)


def _target(root: Path, value, default: Path) -> Path:
    target = Path(value).expanduser() if value is not None else default
    target = target if target.is_absolute() else root / target
    target = target.resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("ASS output path must be inside the workspace") from exc
    return target


def _bounded_metadata(analysis: dict[str, Any]) -> dict[str, Any]:
    stats = analysis["events"]["statistics"]
    resolutions = analysis["fonts"]["resolution"]
    return {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "event_id_strategy": EVENT_ID_STRATEGY_VERSION,
        "event_id_input_logic": EVENT_ID_INPUT_VERSION,
        "text_id_strategy": TEXT_ID_STRATEGY_VERSION,
        "text_id_input_logic": TEXT_ID_INPUT_VERSION,
        "effect_grouper": EFFECT_GROUPER_VERSION,
        "source_subtitle_artifact_id": analysis["source"]["artifact_id"],
        "parser_version": PARSER_VERSION, "classifier_version": CLASSIFIER_VERSION,
        "font_resolver_version": FONT_RESOLVER_VERSION,
        "section_count": len(analysis["document"]["sections"]),
        "style_count": len(analysis["styles"]["items"]),
        "event_count": stats["event_count"],
        "event_id_generated_count": stats["event_id_generated_count"],
        "event_id_null_count": stats["event_id_null_count"],
        "text_id_generated_count": stats["text_id_generated_count"],
        "text_id_null_count": stats["text_id_null_count"],
        "source_ref_count": stats["source_ref_count"],
        "duplicate_identical_event_count": stats["duplicate_identical_event_count"],
        "semantic_group_count": stats["semantic_group_count"],
        "semantic_group_source_count": stats["semantic_group_source_count"],
        "templater_control_count": stats["templater_control_count"],
        "event_id_statuses": stats["event_id_statuses"],
        "dialogue_count": stats["record_types"].get("dialogue", 0),
        "comment_count": stats["record_types"].get("comment", 0),
        "review_count": len(analysis["review_queue"]),
        "font_requirement_count": len(analysis["fonts"]["requirements"]),
        "font_status_counts": dict(__import__("collections").Counter(
            item["status"] for item in resolutions
        )),
    }


def _result_diagnostics(backup_path, analysis, message):
    diagnostics: list[Diagnostic] = []
    if backup_path:
        diagnostics.append(Diagnostic(
            code="artifact_backup_created", message="existing analysis output was backed up",
            context={"path": str(backup_path)},
        ))
    diagnostics.append(Diagnostic(
        code="ass_analyzed", message=message,
        context={
            "event_count": analysis["events"]["statistics"]["event_count"],
            "review_count": len(analysis["review_queue"]),
            "font_requirement_count": len(analysis["fonts"]["requirements"]),
        },
    ))
    if analysis["review_queue"]:
        diagnostics.append(Diagnostic(
            code="ass_review_items", message="ASS analysis produced review queue items",
            level=DiagnosticLevel.WARNING,
            context={"review_count": len(analysis["review_queue"])},
        ))
    return tuple(diagnostics)
