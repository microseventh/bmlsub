"""Compatibility Python API for the migrated subtitle vertical slice."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .ass_analysis import (
    AssAnalysisProfile, AssReconstructionProfile, combine_analyses, export_analysis,
    export_analysis_bundle, get_analysis_event, get_bundle_event, index_analysis_events,
    index_bundle_events, load_analysis, load_analysis_bundle, run_ass_analysis,
    run_ass_normalization, run_ass_reconstruction,
)
from .assets import (
    SourceAssetKind,
    SourceAssetRegistrationOptions,
    episode_manifest,
    run_asset_matching,
    run_match_confirmation,
    run_source_asset_registration,
)
from .credentials import CredentialService
from .credentials.keychain import SecretStore
from .credentials.ssh_config import SSHConfigResolver
from .hanvert import ConverterProvider
from .media import (
    AudioOutputMode,
    FFprobeClient,
    TrackKind,
    VideoPurpose,
    VideoRegistrationOptions,
    get_current_artifact,
    list_current_artifacts,
    list_media_tracks as list_registered_media_tracks,
    resolve_video as resolve_registered_video,
    run_attachment_extraction,
    run_audio_extraction,
    run_subtitle_extraction,
    run_video_registration,
)
from .production.models import ProductionOperation
from .production.requests import create_production_request
from .production.execution import run_production_request
from .release import (
    AnibtPublishProfile, Boto3R2Client, QBittorrentClient, QBittorrentSeedProfile,
    R2Client, R2UploadProfile, RemotePullClient, RemotePullProfile,
    RequestsAnibtClient, SSHQBittorrentClient, SSHRclonePullClient,
    TorrentProfile, TrackerListClient,
    resolve_anibt_credentials, resolve_qbittorrent_credentials, resolve_r2_credentials,
    run_anibt_publish, run_qbittorrent_seed, run_r2_upload, run_remote_pull,
    run_torrent_creation,
)
from .state.models import StageResult, StageStatus
from .state.sqlite_store import SQLiteJobStore
from .subtitle import SubtitleConversionOptions, derive_cht_path, run_subtitle_conversion
from .transcription import TranscriptionMode, TranscriptionOptions, run_transcription


class Pipeline:
    def __init__(self, *, store: SQLiteJobStore | None = None,
                 state_dir: Path | str | None = None,
                 provider: ConverterProvider | None = None,
                 credential_service: CredentialService | None = None) -> None:
        self.store = store
        self.state_dir = state_dir
        self.provider = provider
        self.credential_service = credential_service

    @staticmethod
    def _discover(episode_dir: Path, episode_id: str, kind: str) -> Path | None:
        patterns = (
            f"{episode_id}.{kind}&jpn.ass",
            f"{episode_id}.{kind}.ass",
            f"*{episode_id}*.{kind}&jpn.ass",
            f"*{episode_id}*.{kind}.ass",
        )
        for pattern in patterns:
            matches = sorted(episode_dir.glob(pattern))
            if matches:
                return matches[0].resolve()
        return None

    @staticmethod
    def _result_payload(result: StageResult, *, chs: Path, cht: Path) -> dict[str, Any]:
        output_available = cht.exists()
        succeeded = result.status is StageStatus.SUCCEEDED or (
            result.status is StageStatus.SKIPPED and output_available
        )
        generated = str(cht) if result.status is StageStatus.SUCCEEDED else None
        backups = [
            item.context["path"] for item in result.diagnostics
            if item.code == "artifact_backup_created" and "path" in item.context
        ]
        return {
            "all_ok": succeeded and not result.needs_review,
            "standardized": [],
            "missing": [],
            "generated_cht": generated,
            "backed_up": backups,
            "validated": [str(chs)] + ([str(cht)] if cht.exists() else []),
            "run_id": result.run_id,
            "stage": result.stage_name,
            "status": result.status.value,
            "diagnostics": [item.to_dict() for item in result.diagnostics],
            "error": dict(result.error) if result.error else None,
            "retryable": result.retryable,
            "needs_review": result.needs_review,
            "reused": result.reused,
            "artifacts": [item.to_dict() for item in result.artifacts],
        }

    def validate_subtitles(
        self,
        episode_dir: Path | str,
        episode_id: str,
        source_video: Path | str | None = None,
        chs_subtitle: Path | str | None = None,
        cht_subtitle: Path | str | None = None,
        ensure_cht: bool = False,
        converter: str | None = None,
        api_url: str | None = None,
        timeout: int | None = None,
        regenerate_cht: bool | None = None,
        full_file: bool = False,
        fallback_to_full_file: bool = False,
        force: bool = False,
    ) -> dict[str, Any]:
        del source_video, fallback_to_full_file
        workspace = Path(episode_dir).expanduser().resolve()
        chs = Path(chs_subtitle).expanduser().resolve() if chs_subtitle else self._discover(workspace, episode_id, "chs")
        cht = Path(cht_subtitle).expanduser().resolve() if cht_subtitle else self._discover(workspace, episode_id, "cht")
        if chs is None:
            return {
                "all_ok": False, "standardized": [],
                "missing": [f"{episode_id}.chs&jpn.ass"], "generated_cht": None,
                "backed_up": [], "validated": [str(cht)] if cht else [],
                "run_id": None, "stage": None, "status": "failed",
                "diagnostics": [],
                "error": {"code": "input_missing", "message": "Simplified Chinese subtitle is missing", "retryable": False, "details": {}},
                "retryable": False, "needs_review": False, "reused": False, "artifacts": [],
            }
        target = cht or derive_cht_path(chs)
        if not ensure_cht:
            return {
                "all_ok": target.exists(), "standardized": [],
                "missing": [] if target.exists() else [target.name],
                "generated_cht": None, "backed_up": [],
                "validated": [str(path) for path in (chs, target) if path.exists()],
                "run_id": None, "stage": None,
                "status": "succeeded" if target.exists() else "failed",
                "diagnostics": [], "error": None, "retryable": False,
                "needs_review": False, "reused": False, "artifacts": [],
            }
        rerun = force or regenerate_cht is True
        result = run_subtitle_conversion(
            chs, target,
            workspace=workspace,
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
            force=rerun,
        )
        return self._result_payload(result, chs=chs, cht=target)

    def register_video(
        self,
        video: Path | str,
        *,
        workspace: Path | str,
        episode_id: str,
        purposes: list[str] | tuple[str, ...],
        default_for: list[str] | tuple[str, ...] = (),
        reference: bool = False,
        ffprobe: Path | str = "ffprobe",
        probe_timeout: float = 30.0,
        force: bool = False,
    ) -> dict[str, Any]:
        result = run_video_registration(
            video,
            workspace=workspace,
            episode_id=episode_id,
            options=VideoRegistrationOptions(
                purposes=tuple(VideoPurpose(item) for item in purposes),
                default_for=tuple(VideoPurpose(item) for item in default_for),
                reference=reference,
            ),
            probe=FFprobeClient(ffprobe, timeout=probe_timeout),
            store=self.store,
            state_dir=self.state_dir,
            force=force,
        )
        return result.to_dict()

    def get_asset(self, artifact_id: str, *, workspace: Path | str) -> dict[str, Any] | None:
        store = self.store or SQLiteJobStore.for_workspace(workspace, self.state_dir)
        store.initialize()
        artifact = get_current_artifact(store, artifact_id)
        return artifact.to_dict() if artifact else None

    def list_assets(self, *, workspace: Path | str, episode_id: str | None = None,
                    artifact_type: str | None = None) -> list[dict[str, Any]]:
        store = self.store or SQLiteJobStore.for_workspace(workspace, self.state_dir)
        store.initialize()
        return [
            item.to_dict() for item in list_current_artifacts(
                store, episode_id=episode_id, artifact_type=artifact_type
            )
        ]

    def resolve_video(self, *, workspace: Path | str, episode_id: str,
                      purpose: str) -> dict[str, Any]:
        store = self.store or SQLiteJobStore.for_workspace(workspace, self.state_dir)
        store.initialize()
        artifact, ambiguous = resolve_registered_video(
            store, episode_id, VideoPurpose(purpose)
        )
        return {
            "status": "needs_review" if ambiguous else "succeeded",
            "needs_review": ambiguous,
            "artifact": artifact.to_dict() if artifact else None,
        }

    def register_source_asset(
        self, path: Path | str, *, workspace: Path | str, episode_id: str,
        kind: str, language: str | None = None, force: bool = False,
    ) -> dict[str, Any]:
        result = run_source_asset_registration(
            path, workspace=workspace, episode_id=episode_id,
            options=SourceAssetRegistrationOptions(
                kind=SourceAssetKind(kind), language=language,
            ),
            store=self.store, state_dir=self.state_dir, force=force,
        )
        return result.to_dict()

    def register_subtitle(self, subtitle: Path | str, *, workspace: Path | str,
                          episode_id: str, language: str | None = None,
                          force: bool = False) -> dict[str, Any]:
        return self.register_source_asset(
            subtitle, workspace=workspace, episode_id=episode_id,
            kind="subtitle", language=language, force=force,
        )

    def register_font(self, font: Path | str, *, workspace: Path | str,
                      episode_id: str, force: bool = False) -> dict[str, Any]:
        return self.register_source_asset(
            font, workspace=workspace, episode_id=episode_id,
            kind="font", force=force,
        )

    def register_chapter(self, chapter: Path | str, *, workspace: Path | str,
                         episode_id: str, language: str | None = None,
                         force: bool = False) -> dict[str, Any]:
        return self.register_source_asset(
            chapter, workspace=workspace, episode_id=episode_id,
            kind="chapter", language=language, force=force,
        )

    def register_attachment(self, attachment: Path | str, *, workspace: Path | str,
                            episode_id: str, force: bool = False) -> dict[str, Any]:
        return self.register_source_asset(
            attachment, workspace=workspace, episode_id=episode_id,
            kind="attachment", force=force,
        )

    def match_assets(self, *, workspace: Path | str, episode_id: str,
                     video_artifact_id: str,
                     roles: list[str] | tuple[str, ...] = (
                         "subtitle", "font", "chapter", "attachment",
                     ), force: bool = False,
                     replace_confirmed: bool = False) -> dict[str, Any]:
        result = run_asset_matching(
            workspace=workspace, episode_id=episode_id,
            anchor_artifact_id=video_artifact_id, roles=tuple(roles),
            store=self.store, state_dir=self.state_dir, force=force,
            replace_confirmed=replace_confirmed,
        )
        payload = result.to_dict()
        store = self.store or SQLiteJobStore.for_workspace(workspace, self.state_dir)
        payload["matches"] = [
            item.to_dict() for item in store.list_current_match_sets(episode_id)
            if item.anchor_artifact_id == video_artifact_id and item.input_role in roles
        ]
        return payload

    def confirm_asset_match(self, *, workspace: Path | str, episode_id: str,
                            video_artifact_id: str, role: str,
                            artifact_ids: list[str] | tuple[str, ...],
                            force: bool = False) -> dict[str, Any]:
        result = run_match_confirmation(
            workspace=workspace, episode_id=episode_id,
            anchor_artifact_id=video_artifact_id, role=role,
            artifact_ids=tuple(artifact_ids), store=self.store,
            state_dir=self.state_dir, force=force,
        )
        payload = result.to_dict()
        store = self.store or SQLiteJobStore.for_workspace(workspace, self.state_dir)
        match_set = store.get_current_match_set(episode_id, video_artifact_id, role)
        payload["match"] = match_set.to_dict() if match_set else None
        return payload

    def get_episode_manifest(self, *, workspace: Path | str,
                             episode_id: str) -> dict[str, Any]:
        store = self.store or SQLiteJobStore.for_workspace(workspace, self.state_dir)
        store.initialize()
        return episode_manifest(store, episode_id)

    def list_media_tracks(self, *, workspace: Path | str, episode_id: str,
                          video_artifact_id: str | None = None,
                          purpose: str | None = None,
                          kind: str | None = None) -> dict[str, Any]:
        return list_registered_media_tracks(
            workspace=workspace, episode_id=episode_id,
            video_artifact_id=video_artifact_id, purpose=purpose,
            kind=TrackKind(kind) if kind else None,
            store=self.store, state_dir=self.state_dir,
        )

    def extract_audio_track(self, *, workspace: Path | str, episode_id: str,
                            video_artifact_id: str | None = None,
                            purpose: str | None = None,
                            stream_index: int | None = None,
                            language: str | None = None,
                            mode: str = "both",
                            output_dir: Path | str | None = None,
                            ffmpeg: Path | str = "ffmpeg",
                            ffprobe: Path | str = "ffprobe",
                            process_timeout: float = 600.0,
                            probe_timeout: float = 30.0,
                            force: bool = False) -> dict[str, Any]:
        return run_audio_extraction(
            workspace=workspace, episode_id=episode_id,
            video_artifact_id=video_artifact_id, purpose=purpose,
            stream_index=stream_index, language=language,
            mode=AudioOutputMode(mode), output_dir=output_dir,
            ffmpeg=ffmpeg, ffprobe=ffprobe,
            process_timeout=process_timeout, probe_timeout=probe_timeout,
            store=self.store, state_dir=self.state_dir, force=force,
        ).to_dict()

    def extract_subtitle_track(self, *, workspace: Path | str, episode_id: str,
                               video_artifact_id: str | None = None,
                               purpose: str | None = None,
                               stream_index: int | None = None,
                               language: str | None = None,
                               output_dir: Path | str | None = None,
                               ffmpeg: Path | str = "ffmpeg",
                               ffprobe: Path | str = "ffprobe",
                               process_timeout: float = 300.0,
                               probe_timeout: float = 30.0,
                               force: bool = False) -> dict[str, Any]:
        return run_subtitle_extraction(
            workspace=workspace, episode_id=episode_id,
            video_artifact_id=video_artifact_id, purpose=purpose,
            stream_index=stream_index, language=language,
            output_dir=output_dir, ffmpeg=ffmpeg, ffprobe=ffprobe,
            process_timeout=process_timeout, probe_timeout=probe_timeout,
            store=self.store, state_dir=self.state_dir, force=force,
        ).to_dict()

    def extract_attachments(self, *, workspace: Path | str, episode_id: str,
                            video_artifact_id: str | None = None,
                            purpose: str | None = None,
                            output_dir: Path | str | None = None,
                            ffmpeg: Path | str = "ffmpeg",
                            ffprobe: Path | str = "ffprobe",
                            process_timeout: float = 300.0,
                            probe_timeout: float = 30.0,
                            force: bool = False) -> dict[str, Any]:
        return run_attachment_extraction(
            workspace=workspace, episode_id=episode_id,
            video_artifact_id=video_artifact_id, purpose=purpose,
            output_dir=output_dir, ffmpeg=ffmpeg, ffprobe=ffprobe,
            process_timeout=process_timeout, probe_timeout=probe_timeout,
            store=self.store, state_dir=self.state_dir, force=force,
        ).to_dict()

    def transcribe(self, *, workspace: Path | str, episode_id: str,
                   audio_artifact_id: str, mode: str = "direct",
                   model: str = "mlx-community/whisper-large-v3-turbo",
                   model_revision: str = "main", language: str = "ja",
                   chunk_seconds: float = 240.0, overlap_seconds: float = 5.0,
                   manual_cuts: tuple[float, ...] | list[float] = (),
                   throttle_seconds: float = 0.0,
                   decoding: dict[str, Any] | None = None,
                   output_dir: Path | str | None = None,
                   ffmpeg: Path | str = "ffmpeg",
                   process_timeout: float = 600.0,
                   force: bool = False) -> dict[str, Any]:
        return run_transcription(
            workspace=workspace, episode_id=episode_id,
            audio_artifact_id=audio_artifact_id,
            options=TranscriptionOptions(
                mode=TranscriptionMode(mode), model=model,
                model_revision=model_revision, language=language,
                chunk_seconds=chunk_seconds, overlap_seconds=overlap_seconds,
                manual_cuts=tuple(manual_cuts), throttle_seconds=throttle_seconds,
                decoding=decoding or {},
            ),
            output_dir=output_dir, ffmpeg=ffmpeg,
            process_timeout=process_timeout, store=self.store,
            state_dir=self.state_dir, force=force,
        ).to_dict()

    def analyze_ass(
        self, *, workspace: Path | str, episode_id: str,
        subtitle_artifact_id: str, video_artifact_id: str | None = None,
        font_artifact_ids: tuple[str, ...] = (),
        profile: AssAnalysisProfile | dict[str, Any] | None = None,
        output: Path | str | None = None, force: bool = False,
    ) -> dict[str, Any]:
        return run_ass_analysis(
            workspace=workspace, episode_id=episode_id,
            subtitle_artifact_id=subtitle_artifact_id,
            video_artifact_id=video_artifact_id,
            font_artifact_ids=font_artifact_ids, profile=profile, output=output,
            store=self.store, state_dir=self.state_dir, force=force,
        ).to_dict()

    def normalize_ass(
        self, *, workspace: Path | str, episode_id: str,
        subtitle_artifact_id: str, video_artifact_id: str | None = None,
        font_artifact_ids: tuple[str, ...] = (),
        profile: AssAnalysisProfile | dict[str, Any] | None = None,
        output: Path | str | None = None,
        analysis_output: Path | str | None = None, force: bool = False,
    ) -> dict[str, Any]:
        return run_ass_normalization(
            workspace=workspace, episode_id=episode_id,
            subtitle_artifact_id=subtitle_artifact_id,
            video_artifact_id=video_artifact_id,
            font_artifact_ids=font_artifact_ids, profile=profile, output=output,
            analysis_output=analysis_output, store=self.store,
            state_dir=self.state_dir, force=force,
        ).to_dict()

    def reconstruct_ass(
        self, *, workspace: Path | str, episode_id: str,
        analysis_artifact_id: str,
        profile: AssReconstructionProfile | dict[str, Any] | None = None,
        output: Path | str | None = None, force: bool = False,
    ) -> dict[str, Any]:
        return run_ass_reconstruction(
            workspace=workspace, episode_id=episode_id,
            analysis_artifact_id=analysis_artifact_id, profile=profile,
            output=output, store=self.store, state_dir=self.state_dir, force=force,
        ).to_dict()

    def load_ass_analysis(self, value: Path | str | dict[str, Any], *,
                          allow_legacy: bool = True) -> dict[str, Any]:
        return load_analysis(value, allow_legacy=allow_legacy)

    def export_ass_analysis(self, value: Path | str | dict[str, Any], target: Path | str,
                            *, overwrite: bool = False) -> str:
        payload = load_analysis(value)
        return str(export_analysis(payload, target, overwrite=overwrite))

    def combine_ass_analyses(
        self, values: list[Path | str | dict[str, Any]] | tuple[Path | str | dict[str, Any], ...],
        *, output: Path | str | None = None, overwrite: bool = False,
    ) -> dict[str, Any]:
        bundle = combine_analyses(values)
        if output is not None:
            export_analysis_bundle(bundle, output, overwrite=overwrite)
        return bundle

    def load_ass_analysis_bundle(self, value: Path | str | dict[str, Any]) -> dict[str, Any]:
        return load_analysis_bundle(value)

    def index_ass_analysis_events(
        self, value: Path | str | dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        return index_analysis_events(load_analysis(value))

    def get_ass_analysis_event(
        self, value_or_index: Path | str | dict[str, Any], event_id: str,
    ) -> dict[str, Any] | None:
        if isinstance(value_or_index, (str, Path)):
            value_or_index = load_analysis(value_or_index)
        return get_analysis_event(value_or_index, event_id)

    def index_ass_analysis_bundle_events(
        self, value: Path | str | dict[str, Any],
    ) -> dict[tuple[str, str], dict[str, Any]]:
        return index_bundle_events(load_analysis_bundle(value))

    def get_ass_analysis_bundle_event(
        self, value_or_index: Path | str | dict[str, Any],
        source_artifact_id: str, event_id: str,
    ) -> dict[str, Any] | None:
        if isinstance(value_or_index, (str, Path)):
            value_or_index = load_analysis_bundle(value_or_index)
        return get_bundle_event(value_or_index, source_artifact_id, event_id)

    def create_production_request(
        self, *, workspace: Path | str, episode_id: str, operation: str,
        video_artifact_id: str, subtitle_artifact_id: str | None = None,
        subtitle_artifact_ids: tuple[str, ...] = (), font_artifact_ids: tuple[str, ...] = (),
        chapter_artifact_id: str | None = None,
        attachment_artifact_ids: tuple[str, ...] = (), output_profile: str = "hevc-10bit",
        output_target: Path | str | None = None,
        parameters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request = create_production_request(
            workspace=workspace, episode_id=episode_id,
            operation=ProductionOperation(operation),
            video_artifact_id=video_artifact_id,
            subtitle_artifact_id=subtitle_artifact_id,
            subtitle_artifact_ids=subtitle_artifact_ids,
            font_artifact_ids=font_artifact_ids,
            chapter_artifact_id=chapter_artifact_id,
            attachment_artifact_ids=attachment_artifact_ids,
            output_profile=output_profile, output_target=output_target,
            parameters=parameters, store=self.store, state_dir=self.state_dir,
        )
        return {"status": "succeeded", "request": request.to_dict()}

    def get_production_request(self, request_id: str, *,
                               workspace: Path | str) -> dict[str, Any] | None:
        store = self.store or SQLiteJobStore.for_workspace(workspace, self.state_dir)
        store.initialize()
        request = store.get_production_request(request_id)
        return request.to_dict() if request else None

    def list_production_requests(self, *, workspace: Path | str,
                                 episode_id: str | None = None) -> list[dict[str, Any]]:
        store = self.store or SQLiteJobStore.for_workspace(workspace, self.state_dir)
        store.initialize()
        return [
            item.to_dict() for item in store.list_production_requests(episode_id=episode_id)
        ]

    def execute_production_request(
        self, request_id: str, *, workspace: Path | str,
        ffmpeg: Path | str = "ffmpeg", ffprobe: Path | str = "ffprobe",
        mkvmerge: Path | str = "mkvmerge", process_timeout: float = 7200.0, probe_timeout: float = 30.0,
        force: bool = False,
    ) -> dict[str, Any]:
        result = run_production_request(
            request_id, workspace=workspace, ffmpeg=ffmpeg, ffprobe=ffprobe,
            mkvmerge=mkvmerge, process_timeout=process_timeout, probe_timeout=probe_timeout,
            store=self.store, state_dir=self.state_dir, force=force,
        )
        payload = result.to_dict()
        payload["request"] = self.get_production_request(request_id, workspace=workspace)
        return payload

    def create_torrent(
        self, *, workspace: Path | str, episode_id: str,
        content_artifact_id: str,
        profile: TorrentProfile | dict[str, Any] | None = None,
        output: Path | str | None = None,
        tracker_timeout: float | None = None,
        tracker_client: TrackerListClient | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        return run_torrent_creation(
            workspace=workspace,
            episode_id=episode_id,
            content_artifact_id=content_artifact_id,
            profile=profile,
            output=output,
            tracker_timeout=tracker_timeout,
            tracker_client=tracker_client,
            store=self.store,
            state_dir=self.state_dir,
            force=force,
        ).to_dict()

    def upload_r2(
        self, *, workspace: Path | str, episode_id: str, artifact_id: str,
        profile: R2UploadProfile | dict[str, Any], client: R2Client | None = None,
        account_id_env: str = "R2_ACCOUNT_ID", access_key_env: str = "R2_ACCESS_KEY_ID",
        secret_key_env: str = "R2_SECRET_ACCESS_KEY", endpoint_env: str = "R2_ENDPOINT",
        credential_file: Path | str | None = None,
        credential_manifest: Path | str | None = None,
        credential_profile: str | None = None,
        secret_store: SecretStore | None = None, force: bool = False,
    ) -> dict[str, Any]:
        if credential_profile is None and credential_manifest is not None:
            raise ValueError("R2 credential manifest requires a profile alias")
        credentials = None
        if client is None:
            if credential_profile is not None:
                service = self.credential_service or CredentialService(
                    manifest_path=credential_manifest, secret_store=secret_store,
                )
                credentials = service.resolve_r2(credential_profile)
            else:
                credentials = resolve_r2_credentials(
                    account_id_env=account_id_env, access_key_env=access_key_env,
                    secret_key_env=secret_key_env, endpoint_env=endpoint_env,
                    config_path=credential_file, environment=None,
                )
            client = Boto3R2Client(credentials)
        credential_reference = (
            credentials.reference if credentials is not None
            else f"injected:{type(client).__name__}"
        )
        return run_r2_upload(
            workspace=workspace, episode_id=episode_id, artifact_id=artifact_id,
            profile=profile, client=client, credential_reference=credential_reference,
            store=self.store, state_dir=self.state_dir, force=force,
        ).to_dict()

    def pull_remote(
        self, *, workspace: Path | str, episode_id: str, content_artifact_id: str,
        r2_receipt_artifact_id: str, profile: RemotePullProfile | dict[str, Any],
        client: RemotePullClient | None = None, ssh: Path | str = "ssh",
        connection_manifest: Path | str | None = None, ssh_profile: str | None = None,
        ssh_resolver: SSHConfigResolver | None = None, force: bool = False,
    ) -> dict[str, Any]:
        if connection_manifest is not None or ssh_profile is not None:
            if ssh_profile is None:
                raise ValueError("connection manifest requires an SSH profile")
            service = self.credential_service or CredentialService(
                manifest_path=connection_manifest, ssh_resolver=ssh_resolver,
            )
            ssh_alias, _ = service.resolve_ssh(ssh_profile)
            profile_data = profile.normalized() if isinstance(profile, RemotePullProfile) else dict(profile)
            profile_data.pop("version", None)
            configured = profile_data.get("ssh_alias")
            if configured not in {None, ssh_alias}:
                raise ValueError("remote pull profile SSH alias conflicts with connection manifest")
            profile_data["ssh_alias"] = ssh_alias
            profile = RemotePullProfile.from_mapping(profile_data)
        active_client = client or SSHRclonePullClient(ssh=ssh)
        return run_remote_pull(
            workspace=workspace, episode_id=episode_id,
            content_artifact_id=content_artifact_id,
            r2_receipt_artifact_id=r2_receipt_artifact_id,
            profile=profile, client=active_client, store=self.store,
            state_dir=self.state_dir, force=force,
        ).to_dict()

    def seed_qbittorrent(
        self, *, workspace: Path | str, episode_id: str,
        torrent_artifact_id: str, content_artifact_id: str,
        remote_content_artifact_id: str,
        profile: QBittorrentSeedProfile | dict[str, Any],
        client: QBittorrentClient | None = None, ssh: Path | str = "ssh",
        username_env: str = "QB_USERNAME", password_env: str = "QB_PASSWORD",
        credential_file: Path | str | None = None,
        credential_manifest: Path | str | None = None,
        credential_profile: str | None = None,
        connection_manifest: Path | str | None = None, ssh_profile: str | None = None,
        secret_store: SecretStore | None = None,
        ssh_resolver: SSHConfigResolver | None = None, force: bool = False,
    ) -> dict[str, Any]:
        if connection_manifest is not None or ssh_profile is not None:
            if ssh_profile is None:
                raise ValueError("connection manifest requires an SSH profile")
            service = self.credential_service or CredentialService(
                manifest_path=connection_manifest, ssh_resolver=ssh_resolver,
            )
            ssh_alias, _ = service.resolve_ssh(ssh_profile)
            profile_data = profile.normalized() if isinstance(profile, QBittorrentSeedProfile) else dict(profile)
            profile_data.pop("version", None)
            configured = profile_data.get("ssh_alias")
            if configured not in {None, ssh_alias}:
                raise ValueError("qBittorrent profile SSH alias conflicts with connection manifest")
            profile_data["ssh_alias"] = ssh_alias
            profile = QBittorrentSeedProfile.from_mapping(profile_data)
        if credential_profile is None and credential_manifest is not None:
            raise ValueError("qBittorrent credential manifest requires a profile alias")
        credentials = None
        if client is None:
            if credential_profile is not None:
                service = self.credential_service or CredentialService(
                    manifest_path=credential_manifest, secret_store=secret_store,
                    ssh_resolver=ssh_resolver,
                )
                credentials = service.resolve_qbittorrent(credential_profile)
            else:
                credentials = resolve_qbittorrent_credentials(
                    username_env=username_env, password_env=password_env,
                    config_path=credential_file,
                )
            client = SSHQBittorrentClient(credentials, ssh=ssh)
        credential_reference = (
            credentials.reference if credentials is not None
            else f"injected:{type(client).__name__}"
        )
        return run_qbittorrent_seed(
            workspace=workspace, episode_id=episode_id,
            torrent_artifact_id=torrent_artifact_id,
            content_artifact_id=content_artifact_id,
            remote_content_artifact_id=remote_content_artifact_id,
            profile=profile, client=client, credential_reference=credential_reference,
            store=self.store, state_dir=self.state_dir, force=force,
        ).to_dict()

    def publish_anibt(
        self, *, workspace: Path | str, episode_id: str,
        torrent_artifact_id: str,
        profile: AnibtPublishProfile | dict[str, Any],
        client: "AnibtClient | None" = None,  # type: ignore[name-defined]
        token: str | None = None,
        token_env: str = "ANIBT_TOKEN",
        config_file: Path | str | None = None,
        credential_manifest: Path | str | None = None,
        credential_profile: str | None = None,
        secret_store: SecretStore | None = None,
        api_url: str | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        from .release.anibt import AnibtClient, RequestsAnibtClient
        if credential_profile is not None:
            service = self.credential_service or CredentialService(
                manifest_path=credential_manifest, secret_store=secret_store,
            )
            credentials = service.resolve_anibt(credential_profile, api_url=api_url)
        else:
            if credential_manifest is not None:
                raise ValueError("Anibt credential manifest requires a profile alias")
            credentials = resolve_anibt_credentials(
                token=token, token_env=token_env, config_path=config_file,
                api_url=api_url,
            )
        active_client = client or RequestsAnibtClient()
        return run_anibt_publish(
            workspace=workspace, episode_id=episode_id,
            torrent_artifact_id=torrent_artifact_id,
            profile=profile, client=active_client,
            credential_reference=credentials.reference,
            api_url=credentials.api_url, token=credentials.token,
            store=self.store, state_dir=self.state_dir, force=force,
        ).to_dict()

    def get_run(self, run_id: str, *, workspace: Path | str) -> dict[str, Any] | None:
        store = self.store or SQLiteJobStore.for_workspace(workspace, self.state_dir)
        return store.get_run_detail(run_id)
