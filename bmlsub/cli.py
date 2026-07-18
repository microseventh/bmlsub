"""Command-line interface for the reliable subtitle vertical slice."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
from typing import Any

from .credentials import CredentialService, load_secure_json
from .execution.errors import BmlsubError
from .pipeline import Pipeline
from .state.sqlite_store import SQLiteJobStore
from .version import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bmlsub", description="Reliable BML subtitle workflow")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    episode = commands.add_parser("episode", help="Episode subtitle operations")
    episode_commands = episode.add_subparsers(dest="episode_command", required=True)
    validate = episode_commands.add_parser("validate", help="Validate subtitles and optionally ensure CHT")
    validate.add_argument("--episode-dir", default=".")
    validate.add_argument("--episode-id", required=True)
    validate.add_argument("--chs-subtitle", type=Path)
    validate.add_argument("--cht-subtitle", type=Path)
    validate.add_argument("--ensure-cht", action="store_true")
    validate.add_argument("--converter", default="Taiwan")
    validate.add_argument("--conversion-api-url", default="https://api.zhconvert.org/convert")
    validate.add_argument("--conversion-timeout", type=int, default=60)
    validate.add_argument(
        "--full-file-hanvert", action="store_true",
        help="Explicitly allow high-risk conversion of the complete ASS file",
    )
    validate.add_argument(
        "--no-full-file-fallback", action="store_true",
        help="Deprecated compatibility flag; safe no-fallback behavior is already the default",
    )
    regenerate = validate.add_mutually_exclusive_group()
    regenerate.add_argument("--regenerate-cht", dest="regenerate_cht", action="store_true")
    regenerate.add_argument("--keep-existing-cht", dest="regenerate_cht", action="store_false")
    validate.set_defaults(regenerate_cht=None)
    validate.add_argument("--state-dir", type=Path)
    validate.add_argument("--force", action="store_true")
    validate.set_defaults(handler=_validate)

    asset = commands.add_parser("asset", help="Register and inspect source assets")
    asset_commands = asset.add_subparsers(dest="asset_command", required=True)
    register_video = asset_commands.add_parser(
        "register-video", help="Inspect and register one source video"
    )
    register_video.add_argument("--workspace", default=".")
    register_video.add_argument("--episode-id", required=True)
    register_video.add_argument("--video", type=Path, required=True)
    register_video.add_argument("--purpose", action="append", required=True)
    register_video.add_argument("--default-for", action="append", default=[])
    register_video.add_argument("--reference", action="store_true")
    register_video.add_argument("--ffprobe", default="ffprobe")
    register_video.add_argument("--probe-timeout", type=float, default=30.0)
    register_video.add_argument("--state-dir", type=Path)
    register_video.add_argument("--force", action="store_true")
    register_video.set_defaults(handler=_register_video)

    for command, option, help_text in (
        ("register-subtitle", "subtitle", "Validate and register one external subtitle"),
        ("register-font", "font", "Validate and register one font file"),
        ("register-chapter", "chapter", "Validate and register one chapter file"),
        ("register-attachment", "attachment", "Register one external attachment"),
    ):
        registration = asset_commands.add_parser(command, help=help_text)
        registration.add_argument("--workspace", default=".")
        registration.add_argument("--episode-id", required=True)
        registration.add_argument(f"--{option}", type=Path, required=True)
        if option in {"subtitle", "chapter"}:
            registration.add_argument("--language")
        registration.add_argument("--state-dir", type=Path)
        registration.add_argument("--force", action="store_true")
        registration.set_defaults(handler=_register_source_asset, asset_kind=option)

    match_assets = asset_commands.add_parser("match", help="Create candidate asset relationships")
    match_assets.add_argument("--workspace", default=".")
    match_assets.add_argument("--episode-id", required=True)
    match_assets.add_argument("--video-artifact-id", required=True)
    match_assets.add_argument("--role", action="append", default=[])
    match_assets.add_argument("--replace-confirmed", action="store_true")
    match_assets.add_argument("--state-dir", type=Path)
    match_assets.add_argument("--force", action="store_true")
    match_assets.set_defaults(handler=_match_assets)

    confirm_match = asset_commands.add_parser("confirm", help="Confirm exact asset IDs for one role")
    confirm_match.add_argument("--workspace", default=".")
    confirm_match.add_argument("--episode-id", required=True)
    confirm_match.add_argument("--video-artifact-id", required=True)
    confirm_match.add_argument("--role", required=True)
    confirm_match.add_argument("--artifact-id", action="append", required=True)
    confirm_match.add_argument("--state-dir", type=Path)
    confirm_match.add_argument("--force", action="store_true")
    confirm_match.set_defaults(handler=_confirm_match)

    manifest = asset_commands.add_parser("manifest", help="Show episode assets and match state")
    manifest.add_argument("--workspace", default=".")
    manifest.add_argument("--episode-id", required=True)
    manifest.add_argument("--state-dir", type=Path)
    manifest.set_defaults(handler=_manifest)

    media = commands.add_parser("media", help="Inspect and extract registered media tracks")
    media_commands = media.add_subparsers(dest="media_command", required=True)
    tracks = media_commands.add_parser("tracks", help="List audio or subtitle tracks")
    _add_media_source_arguments(tracks)
    tracks.add_argument("--kind", choices=("audio", "subtitle"))
    tracks.set_defaults(handler=_media_tracks)

    extract_audio = media_commands.add_parser("extract-audio", help="Extract one audio track")
    _add_media_source_arguments(extract_audio)
    extract_audio.add_argument("--stream-index", type=int)
    extract_audio.add_argument("--language")
    extract_audio.add_argument("--mode", choices=("archive", "transcribe", "both"), default="both")
    _add_media_execution_arguments(extract_audio, timeout=600.0)
    extract_audio.set_defaults(handler=_extract_audio)

    extract_subtitle = media_commands.add_parser("extract-subtitle", help="Extract one text subtitle track")
    _add_media_source_arguments(extract_subtitle)
    extract_subtitle.add_argument("--stream-index", type=int)
    extract_subtitle.add_argument("--language")
    _add_media_execution_arguments(extract_subtitle, timeout=300.0)
    extract_subtitle.set_defaults(handler=_extract_subtitle)

    extract_attachments = media_commands.add_parser(
        "extract-attachments", help="Extract all embedded attachments"
    )
    _add_media_source_arguments(extract_attachments)
    _add_media_execution_arguments(extract_attachments, timeout=300.0)
    extract_attachments.set_defaults(handler=_extract_attachments)

    subtitle = commands.add_parser("subtitle", help="Analyze and normalize registered ASS subtitles")
    subtitle_commands = subtitle.add_subparsers(dest="subtitle_command", required=True)
    analyze_ass = subtitle_commands.add_parser("analyze-ass", help="Generate versioned ASS analysis JSON")
    _add_ass_arguments(analyze_ass)
    analyze_ass.add_argument("--output", type=Path)
    analyze_ass.set_defaults(handler=_analyze_ass)
    normalize_ass = subtitle_commands.add_parser("normalize-ass", help="Generate controlled normalized ASS and analysis")
    _add_ass_arguments(normalize_ass)
    normalize_ass.add_argument("--output", type=Path)
    normalize_ass.add_argument("--analysis-output", type=Path)
    normalize_ass.set_defaults(handler=_normalize_ass)
    reconstruct_ass = subtitle_commands.add_parser(
        "reconstruct-ass", help="Rebuild a standard ASS from analysis JSON"
    )
    reconstruct_ass.add_argument("--workspace", default=".")
    reconstruct_ass.add_argument("--episode-id", required=True)
    reconstruct_ass.add_argument("--analysis-artifact-id", required=True)
    reconstruct_ass.add_argument("--profile-json", default="{}")
    reconstruct_ass.add_argument("--output", type=Path)
    reconstruct_ass.add_argument("--state-dir", type=Path)
    reconstruct_ass.add_argument("--force", action="store_true")
    reconstruct_ass.set_defaults(handler=_reconstruct_ass)

    transcribe = commands.add_parser("transcribe", help="Transcribe registered Whisper-ready audio")
    transcribe.add_argument("--workspace", default=".")
    transcribe.add_argument("--episode-id", required=True)
    transcribe.add_argument("--audio-artifact-id", required=True)
    transcribe.add_argument("--mode", choices=("direct", "chunked", "both"), default="direct")
    transcribe.add_argument("--model", default="mlx-community/whisper-large-v3-turbo")
    transcribe.add_argument("--model-revision", default="main")
    transcribe.add_argument("--language", default="ja")
    transcribe.add_argument("--chunk-seconds", type=float, default=240.0)
    transcribe.add_argument("--overlap-seconds", type=float, default=5.0)
    transcribe.add_argument("--manual-cut", action="append", default=[])
    transcribe.add_argument("--throttle-seconds", type=float, default=0.0)
    transcribe.add_argument("--decoding-json", default="{}")
    transcribe.add_argument("--output-dir", type=Path)
    transcribe.add_argument("--ffmpeg", default="ffmpeg")
    transcribe.add_argument("--process-timeout", type=float, default=600.0)
    transcribe.add_argument("--state-dir", type=Path)
    transcribe.add_argument("--force", action="store_true")
    transcribe.set_defaults(handler=_transcribe)

    production = commands.add_parser("production", help="Create and execute production requests")
    production_commands = production.add_subparsers(dest="production_command", required=True)
    create_production = production_commands.add_parser(
        "create", help="Create one explicit production request"
    )
    create_production.add_argument("--workspace", default=".")
    create_production.add_argument("--episode-id", required=True)
    create_production.add_argument(
        "--operation", choices=("encode", "hardsub", "mux_subtitle"), default="encode",
    )
    create_production.add_argument("--video-artifact-id", required=True)
    create_production.add_argument("--subtitle-artifact-id", action="append", default=[])
    create_production.add_argument("--font-artifact-id", action="append", default=[])
    create_production.add_argument("--chapter-artifact-id")
    create_production.add_argument("--attachment-artifact-id", action="append", default=[])
    create_production.add_argument(
        "--output-profile", choices=("hevc-10bit", "h264-chs", "h264-cht", "mkv-subtitle"),
        default="hevc-10bit",
    )
    create_production.add_argument("--output-target", type=Path)
    create_production.add_argument("--parameters-json", default="{}")
    create_production.add_argument("--state-dir", type=Path)
    create_production.set_defaults(handler=_create_production)

    show_production = production_commands.add_parser("show", help="Show one production request")
    show_production.add_argument("request_id")
    show_production.add_argument("--workspace", default=".")
    show_production.add_argument("--state-dir", type=Path)
    show_production.set_defaults(handler=_show_production)

    list_production = production_commands.add_parser("list", help="List production requests")
    list_production.add_argument("--workspace", default=".")
    list_production.add_argument("--episode-id")
    list_production.add_argument("--state-dir", type=Path)
    list_production.set_defaults(handler=_list_production)

    execute_production = production_commands.add_parser(
        "execute", help="Execute one persisted production request"
    )
    execute_production.add_argument("request_id")
    execute_production.add_argument("--workspace", default=".")
    execute_production.add_argument("--ffmpeg", default="ffmpeg")
    execute_production.add_argument("--ffprobe", default="ffprobe")
    execute_production.add_argument("--mkvmerge", default="mkvmerge")
    execute_production.add_argument("--process-timeout", type=float, default=7200.0)
    execute_production.add_argument("--probe-timeout", type=float, default=30.0)
    execute_production.add_argument("--state-dir", type=Path)
    execute_production.add_argument("--force", action="store_true")
    execute_production.set_defaults(handler=_execute_production)

    credentials = commands.add_parser("credentials", help="Manage macOS-backed credential profiles")
    credential_commands = credentials.add_subparsers(dest="credential_command", required=True)
    import_credentials = credential_commands.add_parser(
        "import-json", help="Import a protected JSON bundle into macOS Keychain"
    )
    import_credentials.add_argument("--input", type=Path, required=True)
    import_credentials.add_argument("--manifest", type=Path)
    import_credentials.add_argument("--replace", action="store_true")
    import_credentials.set_defaults(handler=_import_credentials)
    upsert_credentials = credential_commands.add_parser(
        "upsert-secret", help="Add or replace one protected Keychain profile"
    )
    upsert_credentials.add_argument("--manifest", type=Path)
    upsert_credentials.add_argument("--alias", required=True)
    upsert_credentials.add_argument("--kind", choices=("r2", "qbittorrent", "anibt"), required=True)
    upsert_credentials.add_argument("--input", type=Path, required=True)
    upsert_credentials.add_argument("--settings-json", default="{}")
    upsert_credentials.add_argument("--replace", action="store_true")
    upsert_credentials.set_defaults(handler=_upsert_credentials)
    credential_list_parser = credential_commands.add_parser(
        "list", help="List redacted credential profiles"
    )
    credential_list_parser.add_argument("--manifest", type=Path)
    credential_list_parser.set_defaults(handler=_credential_list)
    credential_get_parser = credential_commands.add_parser(
        "get", help="Show one redacted credential profile"
    )
    credential_get_parser.add_argument("--manifest", type=Path)
    credential_get_parser.add_argument("--profile", required=True)
    credential_get_parser.set_defaults(handler=_credential_get)
    create_credentials = credential_commands.add_parser(
        "create", help="Create one credential profile"
    )
    create_credentials.add_argument("--manifest", type=Path)
    create_credentials.add_argument("--alias", required=True)
    create_credentials.add_argument(
        "--kind", choices=("r2", "qbittorrent", "anibt", "ssh", "remote_pull"), required=True,
    )
    create_credentials.add_argument("--input", type=Path)
    create_credentials.add_argument("--settings-json", default="{}")
    create_credentials.add_argument("--label")
    create_credentials.add_argument("--description")
    create_credentials.set_defaults(handler=_create_credentials)
    update_credentials = credential_commands.add_parser(
        "update", help="Update one credential profile"
    )
    update_credentials.add_argument("--manifest", type=Path)
    update_credentials.add_argument("--profile", required=True)
    update_credentials.add_argument("--new-alias")
    update_credentials.add_argument(
        "--kind", choices=("r2", "qbittorrent", "anibt", "ssh", "remote_pull"),
    )
    update_credentials.add_argument("--input", type=Path)
    update_credentials.add_argument("--settings-json")
    update_credentials.add_argument("--label")
    update_credentials.add_argument("--description")
    update_credentials.set_defaults(handler=_update_credentials)
    delete_credentials = credential_commands.add_parser(
        "delete", help="Delete one unreferenced credential profile"
    )
    delete_credentials.add_argument("--manifest", type=Path)
    delete_credentials.add_argument("--profile", required=True)
    delete_credentials.add_argument("--confirm-delete", action="store_true", required=True)
    delete_credentials.set_defaults(handler=_delete_credentials)
    credential_status_parser = credential_commands.add_parser(
        "status", help="Show redacted credential profile availability"
    )
    credential_status_parser.add_argument("--manifest", type=Path)
    credential_status_parser.set_defaults(handler=_credential_status)
    validate_credentials_parser = credential_commands.add_parser(
        "validate", help="Validate Keychain payloads and SSH aliases without network access"
    )
    validate_credentials_parser.add_argument("--manifest", type=Path)
    validate_credentials_parser.set_defaults(handler=_validate_credentials)
    probe_credentials_parser = credential_commands.add_parser(
        "probe", help="Run one bounded read-only external credential check"
    )
    probe_credentials_parser.add_argument("--manifest", type=Path)
    probe_credentials_parser.add_argument("--profile", required=True)
    probe_credentials_parser.add_argument("--connection-profile")
    probe_credentials_parser.add_argument("--probe-json", default="{}")
    probe_credentials_parser.add_argument("--ssh", default="ssh")
    probe_credentials_parser.add_argument(
        "--confirm-external-action", action="store_true", required=True,
    )
    probe_credentials_parser.set_defaults(handler=_probe_credentials)

    release = commands.add_parser("release", help="Create and validate local release artifacts")
    release_commands = release.add_subparsers(dest="release_command", required=True)
    create_torrent = release_commands.add_parser(
        "create-torrent", help="Create a validated BitTorrent v1 file"
    )
    create_torrent.add_argument("--workspace", default=".")
    create_torrent.add_argument("--episode-id", required=True)
    create_torrent.add_argument("--content-artifact-id", required=True)
    create_torrent.add_argument("--profile-json", default="{}")
    create_torrent.add_argument("--output", type=Path)
    create_torrent.add_argument("--tracker-timeout", type=float)
    create_torrent.add_argument("--state-dir", type=Path)
    create_torrent.add_argument("--force", action="store_true")
    create_torrent.set_defaults(handler=_create_torrent)

    upload_r2 = release_commands.add_parser(
        "upload-r2", help="Upload one validated release Artifact to Cloudflare R2"
    )
    upload_r2.add_argument("--workspace", default=".")
    upload_r2.add_argument("--episode-id", required=True)
    upload_r2.add_argument("--artifact-id", required=True)
    upload_r2.add_argument("--profile-json", required=True)
    upload_r2.add_argument("--account-id-env", default="R2_ACCOUNT_ID")
    upload_r2.add_argument("--access-key-env", default="R2_ACCESS_KEY_ID")
    upload_r2.add_argument("--secret-key-env", default="R2_SECRET_ACCESS_KEY")
    upload_r2.add_argument("--endpoint-env", default="R2_ENDPOINT")
    upload_r2.add_argument("--credential-file", type=Path)
    upload_r2.add_argument("--credential-manifest", type=Path)
    upload_r2.add_argument("--credential-profile")
    upload_r2.add_argument("--confirm-external-action", action="store_true", required=True)
    upload_r2.add_argument("--state-dir", type=Path)
    upload_r2.add_argument("--force", action="store_true")
    upload_r2.set_defaults(handler=_upload_r2)

    pull_remote = release_commands.add_parser(
        "pull-remote", help="Pull one verified R2 object on a server through SSH and rclone"
    )
    pull_remote.add_argument("--workspace", default=".")
    pull_remote.add_argument("--episode-id", required=True)
    pull_remote.add_argument("--content-artifact-id", required=True)
    pull_remote.add_argument("--r2-receipt-artifact-id", required=True)
    pull_remote.add_argument("--profile-json", required=True)
    pull_remote.add_argument("--ssh", default="ssh")
    pull_remote.add_argument("--connection-manifest", type=Path)
    pull_remote.add_argument("--ssh-profile")
    pull_remote.add_argument("--confirm-external-action", action="store_true", required=True)
    pull_remote.add_argument("--state-dir", type=Path)
    pull_remote.add_argument("--force", action="store_true")
    pull_remote.set_defaults(handler=_pull_remote)

    seed_qb = release_commands.add_parser(
        "seed-qbittorrent", help="Add and verify one torrent through an SSH-tunneled qBittorrent API"
    )
    seed_qb.add_argument("--workspace", default=".")
    seed_qb.add_argument("--episode-id", required=True)
    seed_qb.add_argument("--torrent-artifact-id", required=True)
    seed_qb.add_argument("--content-artifact-id", required=True)
    seed_qb.add_argument("--remote-content-artifact-id", required=True)
    seed_qb.add_argument("--profile-json", required=True)
    seed_qb.add_argument("--username-env", default="QB_USERNAME")
    seed_qb.add_argument("--password-env", default="QB_PASSWORD")
    seed_qb.add_argument("--credential-file", type=Path)
    seed_qb.add_argument("--credential-manifest", type=Path)
    seed_qb.add_argument("--credential-profile")
    seed_qb.add_argument("--connection-manifest", type=Path)
    seed_qb.add_argument("--ssh-profile")
    seed_qb.add_argument("--ssh", default="ssh")
    seed_qb.add_argument("--confirm-external-action", action="store_true", required=True)
    seed_qb.add_argument("--state-dir", type=Path)
    seed_qb.add_argument("--force", action="store_true")
    seed_qb.set_defaults(handler=_seed_qbittorrent)

    publish_anibt = release_commands.add_parser(
        "publish-anibt", help="Publish one torrent release to anibt.net"
    )
    publish_anibt.add_argument("--workspace", default=".")
    publish_anibt.add_argument("--episode-id", required=True)
    publish_anibt.add_argument("--torrent-artifact-id", required=True)
    publish_anibt.add_argument("--profile-json", required=True)
    publish_anibt.add_argument("--token-env", default="ANIBT_TOKEN")
    publish_anibt.add_argument("--config-file", type=Path)
    publish_anibt.add_argument("--credential-manifest", type=Path)
    publish_anibt.add_argument("--credential-profile")
    publish_anibt.add_argument("--api-url")
    publish_anibt.add_argument("--confirm-external-action", action="store_true", required=True)
    publish_anibt.add_argument("--state-dir", type=Path)
    publish_anibt.add_argument("--force", action="store_true")
    publish_anibt.set_defaults(handler=_publish_anibt)

    workstation = commands.add_parser("workstation", help="Run the three-phase episode workstation")
    workstation_commands = workstation.add_subparsers(dest="workstation_command", required=True)
    workstation_series = workstation_commands.add_parser("series", help="Create or validate series metadata")
    workstation_series_commands = workstation_series.add_subparsers(
        dest="workstation_series_command", required=True,
    )
    workstation_series_show = workstation_series_commands.add_parser(
        "show", help="Validate and show series metadata from one episode directory",
    )
    workstation_series_show.add_argument("--workspace", default=".")
    workstation_series_show.set_defaults(handler=_workstation_series_show)
    workstation_series_create = workstation_series_commands.add_parser(
        "create", help="Create a strictly validated bgminfo/series.json",
    )
    workstation_series_create.add_argument("--parent-dir", type=Path)
    workstation_series_create.add_argument("--series-folder-name")
    workstation_series_create.add_argument("--title-chs")
    workstation_series_create.add_argument("--title-cht")
    workstation_series_create.add_argument("--romanized-title")
    workstation_series_create.add_argument("--group-chs")
    workstation_series_create.add_argument("--group-cht")
    workstation_series_create.add_argument("--bgm-id", type=int)
    workstation_series_create.add_argument("--anime-id")
    workstation_series_create.add_argument("--production-json", default="{}")
    workstation_series_create.add_argument("--publish-json", default="{}")
    workstation_series_create.add_argument("--replace", action="store_true")
    workstation_series_create.add_argument("--interactive", action="store_true")
    workstation_series_create.set_defaults(handler=_workstation_series_create)
    workstation_preprocess = workstation_commands.add_parser(
        "preprocess", help="Inspect and prepare one episode for translation"
    )
    _add_workstation_common_arguments(workstation_preprocess)
    workstation_preprocess.add_argument("--source-video", type=Path)
    workstation_preprocess.add_argument("--reference-language", default="eng")
    workstation_preprocess.add_argument("--reference-stream-index", type=int)
    workstation_preprocess.add_argument("--audio-language", default="jpn")
    workstation_preprocess.add_argument("--audio-stream-index", type=int)
    workstation_preprocess.set_defaults(handler=_workstation_preprocess)

    workstation_delivery = workstation_commands.add_parser(
        "delivery", help="Validate translation inputs and build release products"
    )
    _add_workstation_common_arguments(workstation_delivery)
    workstation_delivery.add_argument(
        "--step",
        choices=(
            "all", "delivery", "validate_subtitles_fonts", "encode_hevc",
            "encode_hardsub_chs", "encode_hardsub_cht", "mux_subtitles",
            "create_torrents",
        ),
        default="all",
        help="Run only one delivery step; dependencies must already be recorded",
    )
    workstation_delivery.add_argument("--production-subtitle", type=Path)
    _add_release_name_arguments(workstation_delivery)
    workstation_delivery.add_argument("--hardsub-parameters-json", default="{}")
    workstation_delivery.add_argument("--hevc-parameters-json", default="{}")
    workstation_delivery.add_argument("--ass-profile-json", default="{}")
    workstation_delivery.add_argument("--torrent-profile-json", default='{"format":"v1"}')
    workstation_delivery.set_defaults(handler=_workstation_delivery)

    workstation_publish = workstation_commands.add_parser(
        "publish", help="Publish completed products through confirmed external stages"
    )
    _add_workstation_common_arguments(workstation_publish)
    workstation_publish.add_argument("--publish-config-json")
    workstation_publish.add_argument("--confirm-external-action", action="store_true")
    workstation_publish.set_defaults(handler=_workstation_publish)

    workstation_status = workstation_commands.add_parser("status", help="Show workstation snapshots")
    workstation_status.add_argument("--workspace", default=".")
    workstation_status.add_argument("--step")
    workstation_status.set_defaults(handler=_workstation_status)

    show_asset = asset_commands.add_parser("show", help="Show one registered asset")
    show_asset.add_argument("artifact_id")
    show_asset.add_argument("--workspace", default=".")
    show_asset.add_argument("--state-dir", type=Path)
    show_asset.set_defaults(handler=_show_asset)

    list_assets = asset_commands.add_parser("list", help="List current registered assets")
    list_assets.add_argument("--workspace", default=".")
    list_assets.add_argument("--episode-id")
    list_assets.add_argument("--type", dest="artifact_type")
    list_assets.add_argument("--state-dir", type=Path)
    list_assets.set_defaults(handler=_list_assets)

    runs = commands.add_parser("run", help="Inspect persisted runs")
    run_commands = runs.add_subparsers(dest="run_command", required=True)
    show = run_commands.add_parser("show", help="Show one persisted run")
    show.add_argument("run_id")
    show.add_argument("--workspace", default=".")
    show.add_argument("--state-dir", type=Path)
    show.set_defaults(handler=_show_run)
    return parser


def _add_workstation_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--episode-id")
    parser.add_argument("--force", action="store_true")


def _add_release_name_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--group-chs")
    parser.add_argument("--group-cht")
    parser.add_argument("--title-chs")
    parser.add_argument("--title-cht")
    parser.add_argument("--romanized-title")


def _add_ass_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--episode-id", required=True)
    parser.add_argument("--subtitle-artifact-id", required=True)
    parser.add_argument("--video-artifact-id")
    parser.add_argument("--font-artifact-id", action="append", default=[])
    parser.add_argument("--profile-json", default="{}")
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--force", action="store_true")


def _add_media_source_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--episode-id", required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--video-artifact-id")
    source.add_argument("--purpose")
    parser.add_argument("--state-dir", type=Path)


def _add_media_execution_arguments(parser: argparse.ArgumentParser, *, timeout: float) -> None:
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    parser.add_argument("--process-timeout", type=float, default=timeout)
    parser.add_argument("--probe-timeout", type=float, default=30.0)
    parser.add_argument("--force", action="store_true")


def _validate(args: argparse.Namespace) -> dict[str, Any]:
    pipeline = Pipeline(state_dir=args.state_dir)
    return pipeline.validate_subtitles(
        args.episode_dir,
        args.episode_id,
        chs_subtitle=args.chs_subtitle,
        cht_subtitle=args.cht_subtitle,
        ensure_cht=args.ensure_cht,
        converter=args.converter,
        api_url=args.conversion_api_url,
        timeout=args.conversion_timeout,
        regenerate_cht=args.regenerate_cht,
        full_file=args.full_file_hanvert,
        fallback_to_full_file=False,
        force=args.force,
    )


def _register_video(args: argparse.Namespace) -> dict[str, Any]:
    pipeline = Pipeline(state_dir=args.state_dir)
    return pipeline.register_video(
        args.video,
        workspace=args.workspace,
        episode_id=args.episode_id,
        purposes=args.purpose,
        default_for=args.default_for,
        reference=args.reference,
        ffprobe=args.ffprobe,
        probe_timeout=args.probe_timeout,
        force=args.force,
    )


def _register_source_asset(args: argparse.Namespace) -> dict[str, Any]:
    pipeline = Pipeline(state_dir=args.state_dir)
    path = getattr(args, args.asset_kind)
    method = getattr(pipeline, f"register_{args.asset_kind}")
    kwargs: dict[str, Any] = {
        "workspace": args.workspace, "episode_id": args.episode_id,
        "force": args.force,
    }
    if hasattr(args, "language"):
        kwargs["language"] = args.language
    return method(path, **kwargs)


def _match_assets(args: argparse.Namespace) -> dict[str, Any]:
    pipeline = Pipeline(state_dir=args.state_dir)
    roles = args.role or ["subtitle", "font", "chapter", "attachment"]
    return pipeline.match_assets(
        workspace=args.workspace, episode_id=args.episode_id,
        video_artifact_id=args.video_artifact_id, roles=roles,
        replace_confirmed=args.replace_confirmed, force=args.force,
    )


def _confirm_match(args: argparse.Namespace) -> dict[str, Any]:
    pipeline = Pipeline(state_dir=args.state_dir)
    return pipeline.confirm_asset_match(
        workspace=args.workspace, episode_id=args.episode_id,
        video_artifact_id=args.video_artifact_id, role=args.role,
        artifact_ids=args.artifact_id, force=args.force,
    )


def _manifest(args: argparse.Namespace) -> dict[str, Any]:
    return Pipeline(state_dir=args.state_dir).get_episode_manifest(
        workspace=args.workspace, episode_id=args.episode_id,
    )


def _media_tracks(args: argparse.Namespace) -> dict[str, Any]:
    return Pipeline(state_dir=args.state_dir).list_media_tracks(
        workspace=args.workspace, episode_id=args.episode_id,
        video_artifact_id=args.video_artifact_id, purpose=args.purpose,
        kind=args.kind,
    )


def _extract_audio(args: argparse.Namespace) -> dict[str, Any]:
    return Pipeline(state_dir=args.state_dir).extract_audio_track(
        workspace=args.workspace, episode_id=args.episode_id,
        video_artifact_id=args.video_artifact_id, purpose=args.purpose,
        stream_index=args.stream_index, language=args.language, mode=args.mode,
        output_dir=args.output_dir, ffmpeg=args.ffmpeg, ffprobe=args.ffprobe,
        process_timeout=args.process_timeout, probe_timeout=args.probe_timeout,
        force=args.force,
    )


def _extract_subtitle(args: argparse.Namespace) -> dict[str, Any]:
    return Pipeline(state_dir=args.state_dir).extract_subtitle_track(
        workspace=args.workspace, episode_id=args.episode_id,
        video_artifact_id=args.video_artifact_id, purpose=args.purpose,
        stream_index=args.stream_index, language=args.language,
        output_dir=args.output_dir, ffmpeg=args.ffmpeg, ffprobe=args.ffprobe,
        process_timeout=args.process_timeout, probe_timeout=args.probe_timeout,
        force=args.force,
    )


def _extract_attachments(args: argparse.Namespace) -> dict[str, Any]:
    return Pipeline(state_dir=args.state_dir).extract_attachments(
        workspace=args.workspace, episode_id=args.episode_id,
        video_artifact_id=args.video_artifact_id, purpose=args.purpose,
        output_dir=args.output_dir, ffmpeg=args.ffmpeg, ffprobe=args.ffprobe,
        process_timeout=args.process_timeout, probe_timeout=args.probe_timeout,
        force=args.force,
    )


def _ass_profile(args: argparse.Namespace) -> dict[str, Any]:
    profile = json.loads(args.profile_json)
    if not isinstance(profile, dict):
        raise ValueError("--profile-json must contain a JSON object")
    return profile


def _analyze_ass(args: argparse.Namespace) -> dict[str, Any]:
    return Pipeline(state_dir=args.state_dir).analyze_ass(
        workspace=args.workspace, episode_id=args.episode_id,
        subtitle_artifact_id=args.subtitle_artifact_id,
        video_artifact_id=args.video_artifact_id,
        font_artifact_ids=tuple(args.font_artifact_id),
        profile=_ass_profile(args), output=args.output, force=args.force,
    )


def _normalize_ass(args: argparse.Namespace) -> dict[str, Any]:
    return Pipeline(state_dir=args.state_dir).normalize_ass(
        workspace=args.workspace, episode_id=args.episode_id,
        subtitle_artifact_id=args.subtitle_artifact_id,
        video_artifact_id=args.video_artifact_id,
        font_artifact_ids=tuple(args.font_artifact_id),
        profile=_ass_profile(args), output=args.output,
        analysis_output=args.analysis_output, force=args.force,
    )


def _reconstruct_ass(args: argparse.Namespace) -> dict[str, Any]:
    return Pipeline(state_dir=args.state_dir).reconstruct_ass(
        workspace=args.workspace, episode_id=args.episode_id,
        analysis_artifact_id=args.analysis_artifact_id,
        profile=_ass_profile(args), output=args.output, force=args.force,
    )


def _transcribe(args: argparse.Namespace) -> dict[str, Any]:
    from .transcription import parse_timestamp

    decoding = json.loads(args.decoding_json)
    if not isinstance(decoding, dict):
        raise ValueError("--decoding-json must contain a JSON object")
    return Pipeline(state_dir=args.state_dir).transcribe(
        workspace=args.workspace, episode_id=args.episode_id,
        audio_artifact_id=args.audio_artifact_id, mode=args.mode,
        model=args.model, model_revision=args.model_revision,
        language=args.language, chunk_seconds=args.chunk_seconds,
        overlap_seconds=args.overlap_seconds,
        manual_cuts=tuple(parse_timestamp(item) for item in args.manual_cut),
        throttle_seconds=args.throttle_seconds, decoding=decoding,
        output_dir=args.output_dir, ffmpeg=args.ffmpeg,
        process_timeout=args.process_timeout, force=args.force,
    )


def _create_production(args: argparse.Namespace) -> dict[str, Any]:
    parameters = json.loads(args.parameters_json)
    if not isinstance(parameters, dict):
        raise ValueError("--parameters-json must contain a JSON object")
    subtitles = tuple(args.subtitle_artifact_id)
    return Pipeline(state_dir=args.state_dir).create_production_request(
        workspace=args.workspace, episode_id=args.episode_id,
        operation=args.operation, video_artifact_id=args.video_artifact_id,
        subtitle_artifact_id=(subtitles[0] if args.operation == "hardsub" and subtitles else None),
        subtitle_artifact_ids=(subtitles if args.operation == "mux_subtitle" else ()),
        font_artifact_ids=tuple(args.font_artifact_id),
        chapter_artifact_id=args.chapter_artifact_id,
        attachment_artifact_ids=tuple(args.attachment_artifact_id),
        output_profile=args.output_profile, output_target=args.output_target,
        parameters=parameters,
    )


def _show_production(args: argparse.Namespace) -> dict[str, Any]:
    request = Pipeline(state_dir=args.state_dir).get_production_request(
        args.request_id, workspace=args.workspace,
    )
    if request is None:
        raise FileNotFoundError(f"production request not found: {args.request_id}")
    return {"status": "succeeded", "request": request}


def _list_production(args: argparse.Namespace) -> dict[str, Any]:
    requests = Pipeline(state_dir=args.state_dir).list_production_requests(
        workspace=args.workspace, episode_id=args.episode_id,
    )
    return {"status": "succeeded", "requests": requests}


def _execute_production(args: argparse.Namespace) -> dict[str, Any]:
    return Pipeline(state_dir=args.state_dir).execute_production_request(
        args.request_id, workspace=args.workspace, ffmpeg=args.ffmpeg,
        ffprobe=args.ffprobe, mkvmerge=args.mkvmerge, process_timeout=args.process_timeout,
        probe_timeout=args.probe_timeout, force=args.force,
    )


def _credential_service(manifest: Path | None) -> CredentialService:
    return CredentialService(manifest_path=manifest)


def _import_credentials(args: argparse.Namespace) -> dict[str, Any]:
    from .credentials import import_credential_json
    service = _credential_service(args.manifest)
    return import_credential_json(
        input_path=args.input, manifest_path=service.manifest_path, replace=args.replace,
    )


def _upsert_credentials(args: argparse.Namespace) -> dict[str, Any]:
    payload = load_secure_json(args.input)
    settings = json.loads(args.settings_json)
    if not isinstance(payload, dict) or not isinstance(settings, dict):
        raise ValueError("credential secret and settings must be JSON objects")
    if args.kind == "anibt" and "api_url" in payload:
        settings.setdefault("api_url", payload.pop("api_url"))
    service = _credential_service(args.manifest)
    if args.replace:
        return service.update_profile(
            args.alias, kind=args.kind, settings=settings, secret=payload,
        )
    return service.create_profile(
        alias=args.alias, kind=args.kind, settings=settings, secret=payload,
    )


def _credential_list(args: argparse.Namespace) -> dict[str, Any]:
    return _credential_service(args.manifest).list_profiles()


def _credential_get(args: argparse.Namespace) -> dict[str, Any]:
    return _credential_service(args.manifest).get_profile(args.profile)


def _credential_input(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    payload = load_secure_json(path)
    if not isinstance(payload, dict):
        raise ValueError("credential secret must be a JSON object")
    return payload


def _create_credentials(args: argparse.Namespace) -> dict[str, Any]:
    settings = json.loads(args.settings_json)
    if not isinstance(settings, dict):
        raise ValueError("--settings-json must contain a JSON object")
    return _credential_service(args.manifest).create_profile(
        alias=args.alias, kind=args.kind, settings=settings,
        secret=_credential_input(args.input), label=args.label,
        description=args.description,
    )


def _update_credentials(args: argparse.Namespace) -> dict[str, Any]:
    settings = None if args.settings_json is None else json.loads(args.settings_json)
    if settings is not None and not isinstance(settings, dict):
        raise ValueError("--settings-json must contain a JSON object")
    return _credential_service(args.manifest).update_profile(
        args.profile, new_alias=args.new_alias, kind=args.kind, settings=settings,
        secret=_credential_input(args.input), label=args.label,
        description=args.description,
    )


def _delete_credentials(args: argparse.Namespace) -> dict[str, Any]:
    return _credential_service(args.manifest).delete_profile(
        args.profile, confirmed=args.confirm_delete,
    )


def _credential_status(args: argparse.Namespace) -> dict[str, Any]:
    return _credential_service(args.manifest).status()


def _validate_credentials(args: argparse.Namespace) -> dict[str, Any]:
    return _credential_service(args.manifest).validate()


def _probe_credentials(args: argparse.Namespace) -> dict[str, Any]:
    probe = json.loads(args.probe_json)
    if not isinstance(probe, dict):
        raise ValueError("--probe-json must contain a JSON object")
    return _credential_service(args.manifest).probe_profile(
        args.profile, connection_profile=args.connection_profile,
        probe=probe, ssh=args.ssh,
    )


def _create_torrent(args: argparse.Namespace) -> dict[str, Any]:
    profile = json.loads(args.profile_json)
    if not isinstance(profile, dict):
        raise ValueError("--profile-json must contain a JSON object")
    return Pipeline(state_dir=args.state_dir).create_torrent(
        workspace=args.workspace,
        episode_id=args.episode_id,
        content_artifact_id=args.content_artifact_id,
        profile=profile,
        output=args.output,
        tracker_timeout=args.tracker_timeout,
        force=args.force,
    )


def _release_profile(value: str) -> dict[str, Any]:
    profile = json.loads(value)
    if not isinstance(profile, dict):
        raise ValueError("--profile-json must contain a JSON object")
    return profile


def _upload_r2(args: argparse.Namespace) -> dict[str, Any]:
    return Pipeline(state_dir=args.state_dir).upload_r2(
        workspace=args.workspace, episode_id=args.episode_id,
        artifact_id=args.artifact_id, profile=_release_profile(args.profile_json),
        account_id_env=args.account_id_env, access_key_env=args.access_key_env,
        secret_key_env=args.secret_key_env, endpoint_env=args.endpoint_env,
        credential_file=args.credential_file,
        credential_manifest=args.credential_manifest,
        credential_profile=args.credential_profile, force=args.force,
    )


def _pull_remote(args: argparse.Namespace) -> dict[str, Any]:
    return Pipeline(state_dir=args.state_dir).pull_remote(
        workspace=args.workspace, episode_id=args.episode_id,
        content_artifact_id=args.content_artifact_id,
        r2_receipt_artifact_id=args.r2_receipt_artifact_id,
        profile=_release_profile(args.profile_json), ssh=args.ssh,
        connection_manifest=args.connection_manifest, ssh_profile=args.ssh_profile,
        force=args.force,
    )


def _seed_qbittorrent(args: argparse.Namespace) -> dict[str, Any]:
    return Pipeline(state_dir=args.state_dir).seed_qbittorrent(
        workspace=args.workspace, episode_id=args.episode_id,
        torrent_artifact_id=args.torrent_artifact_id,
        content_artifact_id=args.content_artifact_id,
        remote_content_artifact_id=args.remote_content_artifact_id,
        profile=_release_profile(args.profile_json), ssh=args.ssh,
        username_env=args.username_env, password_env=args.password_env,
        credential_file=args.credential_file,
        credential_manifest=args.credential_manifest,
        credential_profile=args.credential_profile,
        connection_manifest=args.connection_manifest, ssh_profile=args.ssh_profile,
        force=args.force,
    )


def _publish_anibt(args: argparse.Namespace) -> dict[str, Any]:
    return Pipeline(state_dir=args.state_dir).publish_anibt(
        workspace=args.workspace, episode_id=args.episode_id,
        torrent_artifact_id=args.torrent_artifact_id,
        profile=_release_profile(args.profile_json),
        token_env=args.token_env,
        config_file=args.config_file,
        credential_manifest=args.credential_manifest,
        credential_profile=args.credential_profile,
        api_url=args.api_url,
        force=args.force,
    )


def _workstation_series_show(args: argparse.Namespace) -> dict[str, Any]:
    from .workstation import discover_series_context
    context = discover_series_context(args.workspace)
    return {"status": "succeeded", "series": context.to_dict(),
            "metadata": context.metadata.to_dict()}


def _workstation_series_create(args: argparse.Namespace) -> dict[str, Any]:
    from .workstation import create_series_metadata, prompt_series_metadata
    fields = (
        args.series_folder_name, args.title_chs, args.title_cht, args.romanized_title,
        args.group_chs, args.group_cht, args.bgm_id, args.anime_id,
    )
    if args.interactive:
        if any(value is not None for value in fields):
            raise ValueError("--interactive cannot be combined with series field arguments")
        if args.production_json != "{}" or args.publish_json != "{}":
            raise ValueError("--interactive cannot be combined with profile JSON arguments")
        metadata = prompt_series_metadata(
            parent_dir=args.parent_dir, replace=args.replace,
            output_fn=lambda message: print(message, file=sys.stderr),
        )
    else:
        required = {
            "series_folder_name": args.series_folder_name,
            "title_chs": args.title_chs, "title_cht": args.title_cht,
            "romanized_title": args.romanized_title,
            "group_chs": args.group_chs, "group_cht": args.group_cht,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            raise ValueError(f"series create requires: {', '.join(missing)}")
        metadata = create_series_metadata(
            args.series_folder_name, parent_dir=args.parent_dir,
            title_chs=args.title_chs, title_cht=args.title_cht,
            romanized_title=args.romanized_title,
            group_chs=args.group_chs, group_cht=args.group_cht,
            bgm_id=args.bgm_id, anime_id=args.anime_id,
            production=_json_object(args.production_json, "--production-json"),
            publish=_json_object(args.publish_json, "--publish-json"),
            replace=args.replace,
        )
    return {
        "status": "succeeded", "series_root": str(metadata.path.parent.parent),
        "metadata_path": str(metadata.path), "metadata_hash": metadata.content_hash,
        "metadata": metadata.to_dict(),
    }


def _workstation_preprocess(args: argparse.Namespace) -> dict[str, Any]:
    from .workstation import run_preprocess
    return run_preprocess(
        args.workspace, episode_id=args.episode_id, source_video=args.source_video,
        reference_language=args.reference_language,
        reference_stream_index=args.reference_stream_index,
        audio_language=args.audio_language, audio_stream_index=args.audio_stream_index,
        force=args.force,
    )


def _json_object(value: str, label: str) -> dict[str, Any]:
    payload = json.loads(value)
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return payload


def _workstation_delivery(args: argparse.Namespace) -> dict[str, Any]:
    from .workstation import ReleaseNames, run_delivery, run_delivery_step
    values = (args.group_chs, args.group_cht, args.title_chs, args.title_cht, args.romanized_title)
    if any(value is not None for value in values) and not all(value is not None for value in values):
        raise ValueError("release name overrides must provide all five fields")
    names = ReleaseNames(*values) if all(value is not None for value in values) else None
    kwargs = {
        "episode_id": args.episode_id,
        "production_subtitle": args.production_subtitle,
        "release_names": names,
        "hardsub_parameters": _json_object(args.hardsub_parameters_json, "--hardsub-parameters-json"),
        "hevc_parameters": _json_object(args.hevc_parameters_json, "--hevc-parameters-json"),
        "ass_profile": _json_object(args.ass_profile_json, "--ass-profile-json"),
        "torrent_profile": _json_object(args.torrent_profile_json, "--torrent-profile-json"),
        "force": args.force,
    }
    if args.step not in {"all", "delivery"}:
        return run_delivery_step(args.step, args.workspace, **kwargs)
    return run_delivery(args.workspace, **kwargs)


def _workstation_publish(args: argparse.Namespace) -> dict[str, Any]:
    from .workstation import PublishConfig, run_publish
    config = (PublishConfig(**_json_object(args.publish_config_json, "--publish-config-json"))
              if args.publish_config_json is not None else None)
    return run_publish(
        args.workspace, episode_id=args.episode_id, publish_config=config,
        confirm_external_action=args.confirm_external_action, force=args.force,
    )


def _workstation_status(args: argparse.Namespace) -> dict[str, Any]:
    from .workstation import load_status
    return load_status(args.workspace, args.step)


def _show_asset(args: argparse.Namespace) -> dict[str, Any]:
    pipeline = Pipeline(state_dir=args.state_dir)
    artifact = pipeline.get_asset(args.artifact_id, workspace=args.workspace)
    if artifact is None:
        raise FileNotFoundError(f"asset not found: {args.artifact_id}")
    return {"status": "succeeded", "artifact": artifact}


def _list_assets(args: argparse.Namespace) -> dict[str, Any]:
    pipeline = Pipeline(state_dir=args.state_dir)
    artifacts = pipeline.list_assets(
        workspace=args.workspace,
        episode_id=args.episode_id,
        artifact_type=args.artifact_type,
    )
    return {"status": "succeeded", "artifacts": artifacts}


def _show_run(args: argparse.Namespace) -> dict[str, Any]:
    store = SQLiteJobStore.for_workspace(args.workspace, args.state_dir)
    result = store.get_run_detail(args.run_id)
    if result is None:
        raise FileNotFoundError(f"run not found: {args.run_id}")
    return result


def _exit_code(payload: dict[str, Any]) -> int:
    status = payload.get("status")
    if status == "needs_review":
        return 2
    if status in {"awaiting_confirmation", "blocked", "pending"}:
        return 0
    if status == "failed" or payload.get("error"):
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        incidental = io.StringIO()
        with redirect_stdout(incidental):
            payload = args.handler(args)
        if incidental.getvalue():
            print(incidental.getvalue(), end="", file=sys.stderr)
        for diagnostic in payload.get("diagnostics", []):
            print(json.dumps(diagnostic, ensure_ascii=False), file=sys.stderr)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return _exit_code(payload)
    except BmlsubError as exc:
        payload = {
            "status": "failed",
            "error": exc.to_dict(),
        }
        print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
        return _exit_code(payload)
    except Exception as exc:
        payload = {
            "status": "failed",
            "error": {
                "code": "unexpected", "message": str(exc),
                "retryable": False, "details": {"exception_type": type(exc).__name__},
            },
        }
        print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
