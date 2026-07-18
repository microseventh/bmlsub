"""Reliable subtitle-conversion stage and compatibility facade."""

from __future__ import annotations

from .version import __version__

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit, urlunsplit

from .artifacts.validators import validate_ass_conversion
from .artifacts.writer import ArtifactWriter
from .execution.stage_runner import StageContext, StageOutcome, StageRunner
from .hanvert import ASS_RULE_VERSION, ConverterProvider, HanvertResult, convert_ass, read_ass
from .state.fingerprints import fingerprint_parameters, fingerprint_subtitle, fingerprint_tools
from .state.models import Diagnostic, DiagnosticLevel, StageInputBinding, StageResult, StageStatus, ValidationStatus
from .state.sqlite_store import SQLiteJobStore


SUBTITLE_STAGE_NAME = "subtitle.hanvert"
OUTPUT_FORMAT_VERSION = "ass-cht-v1"
FALLBACK_POLICY_VERSION = "explicit-full-file-v1"


def _public_api_identity(api_url: str) -> str:
    parts = urlsplit(api_url)
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


@dataclass(frozen=True)
class SubtitleConversionOptions:
    converter: str = "Taiwan"
    api_url: str = "https://api.zhconvert.org/convert"
    timeout: int = 60
    full_file: bool = False

    def parameter_values(self) -> dict[str, object]:
        return {
            "converter": self.converter,
            "api_identity": _public_api_identity(self.api_url),
            "timeout": self.timeout,
            "full_file": self.full_file,
            "fallback_policy": FALLBACK_POLICY_VERSION,
            "ass_rule_version": ASS_RULE_VERSION,
            "output_format_version": OUTPUT_FORMAT_VERSION,
        }


def derive_cht_path(chs_path: Path | str) -> Path:
    source = Path(chs_path)
    if ".chs&jpn.ass" in source.name:
        return source.with_name(source.name.replace(".chs&jpn.ass", ".cht&jpn.ass"))
    if ".chs.ass" in source.name:
        return source.with_name(source.name.replace(".chs.ass", ".cht.ass"))
    return source.with_name(f"{source.stem}.cht.ass")


def _diagnostics(result: HanvertResult) -> tuple[Diagnostic, ...]:
    items: list[Diagnostic] = []
    if result.conversion_mode == "full_file":
        items.append(Diagnostic(
            code="full_file_hanvert",
            message="full-file subtitle conversion was explicitly enabled",
            level=DiagnosticLevel.WARNING,
        ))
    if result.no_op_reason:
        items.append(Diagnostic(
            code="subtitle_no_op", message="subtitle conversion made no changes",
            context={"reason": result.no_op_reason},
        ))
    if result.skipped_mixed_groups:
        items.append(Diagnostic(
            code="mixed_groups_skipped",
            message="some mixed-language groups could not be converted reliably",
            level=DiagnosticLevel.WARNING,
            context={"count": result.skipped_mixed_groups},
        ))
    if result.length_changed_events:
        items.append(Diagnostic(
            code="converted_length_changed",
            message="some converted event text changed length",
            level=DiagnosticLevel.WARNING,
            context={"count": result.length_changed_events},
        ))
    return tuple(items)


def run_subtitle_conversion(
    chs_path: Path | str,
    output_path: Path | str | None = None,
    *,
    workspace: Path | str | None = None,
    episode_id: str | None = None,
    options: SubtitleConversionOptions | None = None,
    provider: ConverterProvider | None = None,
    store: SQLiteJobStore | None = None,
    state_dir: Path | str | None = None,
    source_artifact_id: str | None = None,
    artifact_type: str = "subtitle.cht.ass",
    language: str = "zh-hant",
    force: bool = False,
) -> StageResult:
    source = Path(chs_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Simplified Chinese subtitle does not exist: {source}")
    target = Path(output_path).expanduser().resolve() if output_path else derive_cht_path(source).resolve()
    root = Path(workspace).expanduser().resolve() if workspace else source.parent.resolve()
    try:
        source.relative_to(root)
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("subtitle paths must be inside the workspace") from exc
    settings = options or SubtitleConversionOptions()
    input_fingerprint = fingerprint_subtitle(source).digest
    parameter_fingerprint = fingerprint_parameters(settings.parameter_values())
    tool_fingerprint = fingerprint_tools({
        "bmlsub": __version__, "hanvert_rules": ASS_RULE_VERSION,
        "output_format": OUTPUT_FORMAT_VERSION, "provider": settings.converter,
    })
    ledger = store or SQLiteJobStore.for_workspace(root, state_dir)
    ledger.initialize()
    source_artifact = None
    inputs: tuple[StageInputBinding, ...] = ()
    if source_artifact_id is not None:
        source_artifact = ledger.get_artifact(source_artifact_id)
        if (source_artifact is None or source_artifact.validation_status is not ValidationStatus.VALID
                or source_artifact.episode_id != episode_id or source_artifact.path != source):
            raise ValueError("subtitle source artifact is not current")
        inputs = (StageInputBinding(source_artifact.artifact_id, "subtitle", 0),)
    runner = StageRunner(ledger)

    def adapter(context: StageContext) -> StageOutcome:
        source_content, _ = read_ass(source)
        converted = convert_ass(
            source_content,
            converter=settings.converter,
            api_url=settings.api_url,
            timeout=settings.timeout,
            full_file=settings.full_file,
            provider=provider,
        )
        diagnostics = _diagnostics(converted)
        if converted.no_op_reason:
            return StageOutcome(status=StageStatus.SKIPPED, diagnostics=diagnostics)
        written = ArtifactWriter(
            target,
            workspace=root,
            run_id=context.run_id,
            stage_id=context.stage_id,
            artifact_type=artifact_type,
            episode_id=episode_id,
            source_fingerprint=context.input_fingerprint,
            parameter_fingerprint=context.parameter_fingerprint,
            metadata={
                "language": language,
                "source_subtitle_artifact_id": (
                    source_artifact.artifact_id if source_artifact is not None else None
                ),
            },
        ).write(
            lambda temporary: temporary.write_text(converted.content, encoding="utf-8"),
            lambda candidate: validate_ass_conversion(
                source, candidate, allow_full_file=settings.full_file,
            ),
        )
        backup_diagnostic = ()
        if written.backup_path is not None:
            backup_diagnostic = (Diagnostic(
                code="artifact_backup_created",
                message="the previous subtitle was backed up before replacement",
                context={"path": str(written.backup_path)},
            ),)
        return StageOutcome(
            artifacts=(replace(
                written.artifact,
                metadata={
                    key: value for key, value in dict(written.artifact.metadata).items()
                    if value is not None
                },
            ),), diagnostics=diagnostics + backup_diagnostic,
        )

    return runner.run(
        workspace=root,
        command_name="episode.validate",
        stage_name=SUBTITLE_STAGE_NAME,
        episode_id=episode_id,
        input_fingerprint=input_fingerprint,
        parameter_fingerprint=parameter_fingerprint,
        tool_fingerprint=tool_fingerprint,
        adapter=adapter,
        inputs=inputs,
        run_metadata={"input_type": "subtitle.chs.ass", "full_file": settings.full_file},
        force=force,
    )


class SubtitleValidator:
    """Compatibility entry for callers of the legacy subtitle validator."""

    def __init__(self, *, workspace: Path | str | None = None,
                 store: SQLiteJobStore | None = None,
                 state_dir: Path | str | None = None,
                 provider: ConverterProvider | None = None) -> None:
        self.workspace = Path(workspace).resolve() if workspace else None
        self.store = store
        self.state_dir = state_dir
        self.provider = provider
        self.last_result: StageResult | None = None

    def convert_chs_to_cht(
        self,
        chs_path: Path | str,
        output_path: Path | str | None = None,
        *,
        converter: str | None = None,
        api_url: str | None = None,
        timeout: int | None = None,
        backup_existing: bool = True,
        full_file: bool = False,
        fallback_to_full_file: bool = False,
        force: bool = False,
        episode_id: str | None = None,
    ) -> Path:
        del backup_existing, fallback_to_full_file
        source = Path(chs_path).expanduser().resolve()
        target = Path(output_path).expanduser().resolve() if output_path else derive_cht_path(source).resolve()
        result = run_subtitle_conversion(
            source, target,
            workspace=self.workspace or source.parent,
            episode_id=episode_id,
            options=SubtitleConversionOptions(
                converter=converter or "Taiwan",
                api_url=api_url or "https://api.zhconvert.org/convert",
                timeout=timeout if timeout is not None else 60,
                full_file=full_file,
            ),
            provider=self.provider,
            store=self.store,
            state_dir=self.state_dir,
            force=force,
        )
        self.last_result = result
        if result.status in {StageStatus.SUCCEEDED, StageStatus.SKIPPED}:
            return target
        if result.needs_review:
            raise RuntimeError("subtitle conversion requires review")
        raise RuntimeError(result.error["message"] if result.error else "subtitle conversion failed")
