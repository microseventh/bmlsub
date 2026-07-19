"""Command-line interface for the reliable subtitle vertical slice."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from dataclasses import replace
import io
import json
import getpass
from pathlib import Path
import re
import sys
from typing import Any

from .credentials import CredentialService, load_secure_json
from .execution.errors import BmlsubError
from .interactive import (
    confirmation_prompt, default_prompt, optional_prompt, set_ui_language, ui_text,
)
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
    seed_qb.add_argument("--remote-torrent-artifact-id", required=True)
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
    workstation_start = workstation_commands.add_parser(
        "start", help="Discover a series, select an episode, and continue its workflow"
    )
    workstation_start.add_argument("--series-root", type=Path)
    workstation_start.add_argument("--episode-id")
    workstation_start.add_argument("--init-template", action="store_true")
    workstation_start.add_argument("--retry-traditionalization", action="store_true")
    workstation_start.add_argument("--traditionalization-api-url", default="https://api.zhconvert.org/convert")
    workstation_start.add_argument("--traditionalization-converter", default="Taiwan")
    workstation_start.add_argument("--traditionalization-timeout", type=int, default=60)
    workstation_start.add_argument("--execute", action="store_true")
    workstation_start.add_argument("--source-video", type=Path)
    workstation_start.add_argument("--reference-stream-index", type=int)
    workstation_start.add_argument("--audio-stream-index", type=int)
    workstation_start.add_argument("--production-subtitle", type=Path)
    workstation_start.add_argument(
        "--delivery-scope", choices=("full", "mkv", "mp4", "custom"),
    )
    workstation_start.add_argument(
        "--delivery-product", action="append", default=[],
        choices=("mp4_chs", "mp4_cht", "mkv_hevc"),
    )
    workstation_start.add_argument(
        "--delivery-torrents", choices=("selected", "none"), default="selected",
    )
    workstation_start.add_argument("--transcription", choices=("quick", "full", "none"))
    workstation_start.add_argument("--notes-file", type=Path)
    workstation_start.add_argument("--force", action="store_true")
    workstation_start.set_defaults(handler=_workstation_start, workstation_start_command=None)
    workstation_start_commands = workstation_start.add_subparsers(
        dest="workstation_start_command",
    )
    workstation_start_delivery = workstation_start_commands.add_parser(
        "delivery", help="Start explicitly confirmed external file delivery",
    )
    workstation_start_delivery.add_argument("--series-root", type=Path)
    workstation_start_delivery.add_argument("--episode-id")
    workstation_start_delivery.add_argument("--publish-config-json")
    workstation_start_delivery.add_argument(
        "--configure", action="store_true",
        help="Force the interactive delivery configuration wizard",
    )
    workstation_start_delivery.add_argument("--credential-manifest", type=Path)
    workstation_start_delivery.add_argument("--execute", action="store_true")
    workstation_start_delivery.add_argument("-y", "--yes", action="store_true",
                                             help="Run non-interactively using existing configuration")
    workstation_start_delivery.add_argument("--verbose-plan", action="store_true")
    recovery = workstation_start_delivery.add_mutually_exclusive_group()
    recovery.add_argument("--resume", action="store_true",
                          help="Continue while reusing valid completed stages")
    recovery.add_argument("--restart", action="store_true",
                          help="Re-evaluate all stages and reuse valid fingerprints (default)")
    workstation_start_delivery.add_argument("--confirm-external-action", action="store_true")
    workstation_start_delivery.add_argument("--force", action="store_true")
    workstation_start_delivery.set_defaults(handler=_workstation_start_delivery)
    workstation_rebuild = workstation_commands.add_parser(
        "rebuild", help="Force-rebuild one local workstation stage without publishing"
    )
    workstation_rebuild.add_argument("--series-root", type=Path)
    workstation_rebuild.add_argument("--episode-id")
    workstation_rebuild.add_argument(
        "--target", choices=(
            "preprocess", "delivery", "validate_subtitles_fonts", "encode_hevc",
            "encode_hardsub_chs", "encode_hardsub_cht", "mux_subtitles",
            "create_torrents",
        ),
    )
    workstation_rebuild.add_argument("--transcription", choices=("quick", "full", "none"))
    workstation_rebuild.add_argument("--source-video", type=Path)
    workstation_rebuild.add_argument("--reference-stream-index", type=int)
    workstation_rebuild.add_argument("--audio-stream-index", type=int)
    workstation_rebuild.add_argument("--production-subtitle", type=Path)
    workstation_rebuild.add_argument("--confirm-rebuild", action="store_true")
    workstation_rebuild.set_defaults(handler=_workstation_rebuild)
    workstation_series = workstation_commands.add_parser("series", help="Create or validate series metadata")
    workstation_series_commands = workstation_series.add_subparsers(
        dest="workstation_series_command", required=True,
    )
    workstation_series_show = workstation_series_commands.add_parser(
        "show", help="Validate and show series metadata from one episode directory",
    )
    workstation_series_show.add_argument("--workspace", default=".")
    workstation_series_show.set_defaults(handler=_workstation_series_show)
    workstation_series_retry = workstation_series_commands.add_parser(
        "retry-traditionalization", help="Retry pending traditional series names"
    )
    workstation_series_retry.add_argument("--series-root", type=Path)
    workstation_series_retry.add_argument("--converter", default="Taiwan")
    workstation_series_retry.add_argument("--api-url", default="https://api.zhconvert.org/convert")
    workstation_series_retry.add_argument("--timeout", type=int, default=60)
    workstation_series_retry.set_defaults(handler=_workstation_series_retry_traditionalization)
    workstation_series_create = workstation_series_commands.add_parser(
        "create", help="Create a strictly validated bgminfo/series.json",
    )
    workstation_series_create.add_argument("--parent-dir", type=Path)
    workstation_series_create.add_argument("--series-folder-name")
    workstation_series_create.add_argument("--title-chs")
    workstation_series_create.add_argument("--title-cht", help=argparse.SUPPRESS)
    workstation_series_create.add_argument("--romanized-title")
    workstation_series_create.add_argument("--group-chs")
    workstation_series_create.add_argument("--group-cht", help=argparse.SUPPRESS)
    workstation_series_create.add_argument("--bgm-id", type=int)
    workstation_series_create.add_argument("--anime-id")
    workstation_series_create.add_argument("--production-json", default="{}")
    workstation_series_create.add_argument("--publish-json", default="{}", help=argparse.SUPPRESS)
    workstation_series_create.add_argument("--notes-file", type=Path)
    workstation_series_create.add_argument("--traditionalization-api-url", default="https://api.zhconvert.org/convert")
    workstation_series_create.add_argument("--traditionalization-converter", default="Taiwan")
    workstation_series_create.add_argument("--traditionalization-timeout", type=int, default=60)
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
    workstation_preprocess.add_argument(
        "--transcription", choices=("quick", "full", "none"), default="full",
    )
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
    workstation_delivery.add_argument(
        "--delivery-scope", choices=("full", "mkv", "mp4", "custom"), default="full",
    )
    workstation_delivery.add_argument(
        "--delivery-product", action="append", default=[],
        choices=("mp4_chs", "mp4_cht", "mkv_hevc"),
    )
    workstation_delivery.add_argument(
        "--delivery-torrents", choices=("selected", "none"), default="selected",
    )
    _add_release_name_arguments(workstation_delivery)
    workstation_delivery.add_argument("--hardsub-parameters-json")
    workstation_delivery.add_argument("--hevc-parameters-json")
    workstation_delivery.add_argument("--ass-profile-json")
    workstation_delivery.add_argument("--torrent-profile-json")
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
        remote_torrent_artifact_id=args.remote_torrent_artifact_id,
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


_ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|[@-_])")


def _clean_terminal_input(value: str) -> str:
    cleaned = _ANSI_ESCAPE.sub("", value)
    return "".join(character for character in cleaned if character == "\t" or ord(character) >= 32).strip()


def _prompt_stderr(prompt: str) -> str:
    print(prompt, end="", file=sys.stderr, flush=True)
    return _clean_terminal_input(sys.stdin.readline())


def _select_ui_language() -> str:
    print("界面语言 / Interface language:", file=sys.stderr)
    print("  1. 中文", file=sys.stderr)
    print("  2. English", file=sys.stderr)
    selected = _prompt_stderr("请选择 / Select (直接按 Enter 使用默认值：中文 / Press Enter to use the default: 中文): ")
    language = "en" if selected == "2" else "zh"
    if selected not in {"", "1", "2"}:
        raise ValueError("界面语言选择无效 / invalid interface language selection")
    set_ui_language(language)
    return language


def _ensure_ui_language() -> None:
    if sys.stdin.isatty():
        _select_ui_language()
    else:
        set_ui_language("zh")


def _read_notes_file(path: Path | None) -> str | None:
    if path is None:
        return None
    value = path.expanduser().read_text(encoding="utf-8")
    if "\x00" in value:
        raise ValueError("notes file must not contain NUL")
    return value.replace("\r\n", "\n").replace("\r", "\n").strip() or None


def _prompt_markdown_notes() -> str | None:
    choice = _prompt_stderr(ui_text(
        "发布说明（Markdown；直接按 Enter 跳过，输入 f 读取文件，输入 p 粘贴多行）: ",
        "Release notes (Markdown; press Enter to skip, enter f to read a file, or p to paste multiple lines): ",
    )).lower()
    if not choice:
        return None
    if choice == "f":
        path = Path(_prompt_stderr(ui_text("Markdown 文件路径: ", "Markdown file path: "))).expanduser()
        return _read_notes_file(path)
    if choice == "p":
        print(ui_text(
            "请粘贴 Markdown；以单独一行 .end 结束。",
            "Paste Markdown text; finish with .end on a line by itself.",
        ), file=sys.stderr)
        lines = []
        while True:
            line = sys.stdin.readline()
            if line == "":
                break
            line = line.rstrip("\r\n")
            if line == ".end":
                break
            if "\x00" in line:
                raise ValueError("publish notes must not contain NUL")
            lines.append(line)
        return "\n".join(lines).strip() or None
    raise ValueError("NOTE input must be empty, f, or p")


def _prompt_transcription_mode(default: str = "full") -> str:
    print(ui_text("是否执行转录：", "Transcription mode:"), file=sys.stderr)
    print(ui_text("  1. 快速转录一次", "  1. One quick transcription"), file=sys.stderr)
    print(ui_text("  2. 完整转录 + 切片转录（默认）", "  2. Full plus chunked transcription (default)"), file=sys.stderr)
    print(ui_text("  3. 不转录", "  3. Do not transcribe"), file=sys.stderr)
    value = _prompt_default(ui_text("请选择 1、2 或 3", "Select 1, 2, or 3"), "2")
    return {"1": "quick", "2": "full", "3": "none"}.get(value, value)


def _prompt_delivery_selection():
    from .workstation import DeliverySelection
    print(ui_text("请选择本地压制范围：", "Select the local production scope:"), file=sys.stderr)
    print(ui_text(
        "  1. 完整压制：简体 MP4 + 繁体 MP4 + 简繁内封 MKV + Torrent（默认）",
        "  1. Full: Simplified MP4 + Traditional MP4 + bilingual MKV + Torrent (default)",
    ), file=sys.stderr)
    print(ui_text("  2. 仅简繁内封 MKV", "  2. Bilingual MKV only"), file=sys.stderr)
    print(ui_text("  3. 仅简繁内嵌 MP4", "  3. Simplified and Traditional MP4 only"), file=sys.stderr)
    print(ui_text("  4. 自定义产品", "  4. Custom products"), file=sys.stderr)
    value = _prompt_default(ui_text("请选择 1、2、3 或 4", "Select 1, 2, 3, or 4"), "1")
    scope = {"1": "full", "2": "mkv", "3": "mp4", "4": "custom"}.get(value, value)
    products = []
    if scope == "custom":
        if _confirm_stderr(ui_text("生成简体 H.264 内嵌 MP4", "Create the Simplified H.264 hardsub MP4")):
            products.append("mp4_chs")
        if _confirm_stderr(ui_text("生成繁体 H.264 内嵌 MP4", "Create the Traditional H.264 hardsub MP4")):
            products.append("mp4_cht")
        if _confirm_stderr(ui_text("生成 HEVC 10-bit 简繁字幕内封 MKV", "Create the HEVC 10-bit bilingual softsub MKV")):
            products.append("mkv_hevc")
    torrents = True
    if scope != "full":
        torrents = _confirm_stderr(
            ui_text("为所选产品制作 Torrent", "Create Torrents for the selected products"),
            default=True,
        )
    return DeliverySelection.for_scope(scope, products=products, create_torrents=torrents)


def _delivery_selection_from_args(args: argparse.Namespace, *, interactive: bool = False):
    from .workstation import DeliverySelection
    scope = getattr(args, "delivery_scope", None)
    products = tuple(getattr(args, "delivery_product", ()) or ())
    torrents = getattr(args, "delivery_torrents", "selected") == "selected"
    if scope is None and interactive:
        return _prompt_delivery_selection()
    return DeliverySelection.for_scope(
        scope or "full", products=products, create_torrents=torrents,
    )


def _print_delivery_plan(plan: dict[str, Any]) -> None:
    print(ui_text("本地压制方案：", "Local production plan:"), file=sys.stderr)
    print(ui_text(f"  正式字幕: {plan.get('production_subtitle')}", f"  Production subtitle: {plan.get('production_subtitle')}"), file=sys.stderr)
    print(ui_text(f"  字体数量: {plan.get('font_count', 0)}", f"  Font count: {plan.get('font_count', 0)}"), file=sys.stderr)
    print(ui_text(f"  发布命名: {json.dumps(plan.get('release_names'), ensure_ascii=False)}", f"  Release names: {json.dumps(plan.get('release_names'), ensure_ascii=False)}"), file=sys.stderr)
    print(ui_text(f"  HEVC 参数: {json.dumps(plan.get('profiles', {}).get('hevc'), ensure_ascii=False)}", f"  HEVC parameters: {json.dumps(plan.get('profiles', {}).get('hevc'), ensure_ascii=False)}"), file=sys.stderr)
    print(ui_text(f"  H.264 参数: {json.dumps(plan.get('profiles', {}).get('hardsub'), ensure_ascii=False)}", f"  H.264 parameters: {json.dumps(plan.get('profiles', {}).get('hardsub'), ensure_ascii=False)}"), file=sys.stderr)
    print(ui_text("  执行步骤:", "  Execution steps:"), file=sys.stderr)
    for step in plan.get("steps", []):
        print(f"    - {step}", file=sys.stderr)
    print(ui_text("  输出文件:", "  Output files:"), file=sys.stderr)
    intermediate = plan.get("targets", {}).get("hevc_intermediate")
    if intermediate:
        print(f"    - {intermediate}", file=sys.stderr)
    for path in plan.get("targets", {}).get("products", {}).values():
        print(f"    - {path}", file=sys.stderr)
    for path in plan.get("targets", {}).get("torrents", {}).values():
        print(f"    - {path}", file=sys.stderr)
    registered = plan.get("registered", {})
    if registered.get("products") or registered.get("torrents"):
        print(ui_text(
            f"  已登记可复用: {json.dumps(registered, ensure_ascii=False)}",
            f"  Registered and reusable: {json.dumps(registered, ensure_ascii=False)}",
        ), file=sys.stderr)


def _confirm_stderr(prompt: str, *, default: bool = False) -> bool:
    value = _prompt_stderr(confirmation_prompt(prompt, default=default)).lower()
    if not value:
        return default
    return value in {"y", "yes"}


def _workstation_start(args: argparse.Namespace) -> dict[str, Any]:
    _ensure_ui_language()
    from .workstation import (
        execute_recommended_action, inspect_episode_stage, inspect_series_workspace,
        prompt_series_metadata, resolve_series_root, write_series_metadata_template,
    )
    root = resolve_series_root(args.series_root)
    print(ui_text(f"番组根目录: {root}", f"Series root: {root}"), file=sys.stderr)
    series = inspect_series_workspace(root)
    blocking_codes = {item.get("code") for item in series["blocking"]}
    if "series_metadata_missing" in blocking_codes:
        if args.init_template:
            template = write_series_metadata_template(root)
            return {
                "status": "needs_review", "series_root": str(root),
                "template_path": str(template),
                "next_action": "complete_template_and_save_as_bgminfo/series.json",
            }
        if sys.stdin.isatty():
            print(ui_text(
                "未找到 bgminfo/series.json，将进入问答初始化。",
                "bgminfo/series.json was not found. Starting the setup wizard.",
            ), file=sys.stderr)
            def start_input(prompt: str) -> str:
                folder_labels = ("番组文件夹名", "Series folder name")
                if prompt.startswith(folder_labels):
                    print(f"{prompt}{root.name}", file=sys.stderr)
                    return root.name
                return _prompt_stderr(prompt)

            notes_value = _read_notes_file(args.notes_file)
            metadata = prompt_series_metadata(
                parent_dir=root.parent, input_fn=start_input,
                output_fn=lambda message: print(message, file=sys.stderr),
                notes_fn=(lambda: notes_value) if args.notes_file else _prompt_markdown_notes,
            )
            if metadata.path.parent.parent != root:
                raise ValueError("series folder name must match the selected series root")
            from .workstation import ensure_traditional_series_names
            traditional = ensure_traditional_series_names(
                metadata.path, converter=args.traditionalization_converter,
                api_url=args.traditionalization_api_url,
                timeout=args.traditionalization_timeout,
            )
            if traditional["status"] != "resolved":
                return {"status": "needs_review", **traditional,
                        "series_root": str(root),
                        "next_action": "retry_traditionalization"}
            series = inspect_series_workspace(root)
        else:
            return series
    if series["status"] != "succeeded":
        codes = {item.get("code") for item in series["blocking"]}
        if "series_traditionalization_pending" in codes:
            should_retry = args.retry_traditionalization
            if not should_retry and sys.stdin.isatty():
                should_retry = _confirm_stderr(ui_text(
                    "重试未完成的繁体番名或制作组名称转换",
                    "Retry the incomplete Traditional Chinese series or release group name conversion",
                ))
            if should_retry:
                from .workstation import ensure_traditional_series_names
                result = ensure_traditional_series_names(
                    root / "bgminfo" / "series.json",
                    converter=args.traditionalization_converter,
                    api_url=args.traditionalization_api_url,
                    timeout=args.traditionalization_timeout,
                )
                if result["status"] != "resolved":
                    return {"status": "needs_review", **result,
                            "next_action": "retry_traditionalization"}
                series = inspect_series_workspace(root)
        if series["status"] != "succeeded":
            return series
    episodes = series["episodes"]
    episode_id = args.episode_id
    if episode_id is None:
        if not sys.stdin.isatty():
            return {
                **series, "status": "needs_review",
                "next_action": "provide_episode_id",
            }
        print(ui_text("可用单集目录:", "Available episode directories:"), file=sys.stderr)
        for index, item in enumerate(episodes, 1):
            print(f"  {index}. {item['episode_id']}  {item['episode_dir']}", file=sys.stderr)
        selection = _prompt_stderr(ui_text("请选择序号或输入单集目录名: ", "Select a number or enter an episode directory name: "))
        if selection.isdigit() and 1 <= int(selection) <= len(episodes):
            episode_id = episodes[int(selection) - 1]["episode_id"]
        else:
            episode_id = selection
    inspection = inspect_episode_stage(
        root, episode_id, source_video=args.source_video,
        production_subtitle=args.production_subtitle,
    )
    print(ui_text(f"本次单集工作目录: {inspection['episode_dir']}", f"Episode workspace: {inspection['episode_dir']}"), file=sys.stderr)
    print(ui_text(
        f"识别阶段: {inspection['detected_phase']} ({inspection['confidence']})",
        f"Detected phase: {inspection['detected_phase']} ({inspection['confidence']})",
    ), file=sys.stderr)
    for evidence in inspection["evidence"]:
        print(ui_text(f"依据: {json.dumps(evidence, ensure_ascii=False)}", f"Evidence: {json.dumps(evidence, ensure_ascii=False)}"), file=sys.stderr)
    for item in inspection["blocking"]:
        print(ui_text(f"阻断: {json.dumps(item, ensure_ascii=False)}", f"Blocker: {json.dumps(item, ensure_ascii=False)}"), file=sys.stderr)
    if inspection.get("recommended_action") == "run_publish":
        return {
            **inspection,
            "status": "succeeded",
            "executable": False,
            "recommended_action": None,
            "recommended_command": "bmlsub workstation start delivery",
            "next_action": "start_delivery",
        }
    if not inspection["executable"]:
        return inspection
    execute = args.execute
    delivery_selection = None
    if inspection["recommended_action"] == "run_delivery":
        from .workstation import plan_delivery_execution
        delivery_selection = _delivery_selection_from_args(
            args, interactive=sys.stdin.isatty() and args.delivery_scope is None,
        )
        delivery_plan = plan_delivery_execution(
            inspection["episode_dir"], episode_id=inspection["episode_id"],
            production_subtitle=args.production_subtitle,
            selection=delivery_selection,
        )
        _print_delivery_plan(delivery_plan)
        if delivery_plan["status"] != "succeeded":
            return delivery_plan
        if not execute and sys.stdin.isatty():
            execute = _confirm_stderr(ui_text("按以上方案执行本地压制", "Run local production using the plan above"))
        if not execute:
            return {
                "status": "awaiting_confirmation",
                "inspection": inspection,
                "plan": delivery_plan,
                "next_action": "confirm_delivery_plan",
            }
    elif not execute and sys.stdin.isatty():
        execute = _confirm_stderr(f"是否执行 {inspection['recommended_action']}")
    transcription = args.transcription
    if inspection["recommended_action"] == "run_preprocess":
        if transcription is None and sys.stdin.isatty():
            transcription = _prompt_transcription_mode()
        transcription = transcription or "full"
    from .workstation import transcription_jobs_for_mode
    whisper_jobs = transcription_jobs_for_mode(transcription) if transcription else ()
    return execute_recommended_action(
        inspection, confirmed=execute,
        confirm_external_action=False, force=args.force,
        source_video=args.source_video,
        reference_stream_index=args.reference_stream_index,
        audio_stream_index=args.audio_stream_index,
        production_subtitle=args.production_subtitle, whisper_jobs=whisper_jobs,
        delivery_selection=delivery_selection,
    )


def _secret_stderr(prompt: str) -> str:
    value = getpass.getpass(prompt=f"{prompt}: ", stream=sys.stderr)
    if not value or "\x00" in value:
        raise ValueError("secret value must not be empty")
    return value


def _prompt_default(prompt: str, default: str) -> str:
    value = _prompt_stderr(default_prompt(prompt, default))
    return value or default


def _prompt_profile_values(kind: str, *, current: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any] | None]:
    existing = current or {}
    if kind == "r2":
        account_id = _prompt_stderr(ui_text("R2 账户 ID: ", "R2 account ID: "))
        access_key_id = _prompt_stderr(ui_text("R2 Access Key ID: ", "R2 access key ID: "))
        secret = _secret_stderr(ui_text("R2 Secret Access Key", "R2 secret access key"))
        endpoint = _prompt_stderr(optional_prompt(ui_text("R2 endpoint（留空时自动生成）", "R2 endpoint (generated automatically when blank)")))
        payload = {
            "account_id": account_id, "access_key_id": access_key_id,
            "secret_access_key": secret,
        }
        if endpoint:
            payload["endpoint"] = endpoint
        return {}, payload
    if kind == "qbittorrent":
        username = _prompt_stderr(ui_text("qBittorrent 用户名: ", "qBittorrent username: "))
        password = _secret_stderr(ui_text("qBittorrent 密码", "qBittorrent password"))
        return {}, {"username": username, "password": password}
    if kind == "anibt":
        token = _secret_stderr("Anibt Token")
        api_url = _prompt_default(
            ui_text("Anibt API 地址", "Anibt API URL"),
            str(existing.get("api_url", "https://anibt.net/api/releases/publish")),
        )
        return {"api_url": api_url}, {"token": token}
    if kind == "ssh":
        from .credentials import SSHConfigResolver
        default_alias = str(existing.get("ssh_alias", ""))
        label = ui_text(
            "OpenSSH Host 别名（~/.ssh/config 中的 Host，不是 bmlsub 凭据配置名称）",
            "OpenSSH Host alias (the Host in ~/.ssh/config, not the bmlsub credential profile name)",
        )
        ssh_alias = (_prompt_default(label, default_alias)
                     if default_alias else _prompt_stderr(f"{label}: "))
        identity = SSHConfigResolver().resolve(ssh_alias)
        print(ui_text(
            f"已解析 SSH 连接：{json.dumps(identity.bounded(), ensure_ascii=False)}",
            f"Resolved SSH connection: {json.dumps(identity.bounded(), ensure_ascii=False)}",
        ), file=sys.stderr)
        return {
            "ssh_alias": ssh_alias, "expected_host": identity.host,
            "expected_user": identity.user, "expected_port": identity.port,
        }, None
    raise ValueError(f"unsupported credential kind: {kind}")


def _choose_or_create_profile(
    service: CredentialService, kind: str, *, preferred_alias: str | None = None,
) -> tuple[str, str]:
    profiles = [item for item in service.list_profiles()["profiles"] if item["kind"] == kind]
    if preferred_alias:
        profiles.sort(key=lambda item: item["alias"] != preferred_alias)
    actions = []
    for profile in profiles:
        action = "reuse" if profile.get("available") else "repair"
        actions.append((action, profile))
    if actions:
        print(ui_text(
            f"{kind} 凭据配置操作：",
            f"{kind} credential profile action:",
        ), file=sys.stderr)
        for index, (action, profile) in enumerate(actions, 1):
            label = ui_text("复用", "Reuse") if action == "reuse" else ui_text("修复", "Repair")
            marker = ui_text("可用", "available") if profile.get("available") else ui_text("不可用", "unavailable")
            print(f"  {index}. {label} {profile['alias']} ({marker})", file=sys.stderr)
        print(ui_text(
            f"  {len(actions) + 1}. 新建凭据配置",
            f"  {len(actions) + 1}. Create a credential profile",
        ), file=sys.stderr)
        selected = _prompt_default(ui_text("请选择序号", "Select a number"), "1")
        if selected.isdigit() and 1 <= int(selected) <= len(actions):
            action, profile = actions[int(selected) - 1]
            alias = str(profile["alias"])
            if action == "reuse":
                service.validate_profile(alias)
                return alias, "reused"
            settings, secret = _prompt_profile_values(kind, current=profile)
            service.update_profile(alias, settings=settings, secret=secret)
            service.validate_profile(alias)
            return alias, "repaired"
        if selected != str(len(actions) + 1):
            raise ValueError("credential profile selection is invalid")
    alias = _prompt_stderr(ui_text(
        f"新建 {kind} 凭据配置名称（仅用于 bmlsub 凭据清单）: ",
        f"New {kind} credential profile name (used only in the bmlsub credential manifest): ",
    ))
    settings, secret = _prompt_profile_values(kind)
    service.create_profile(
        alias=alias, kind=kind, settings=settings, secret=secret,
    )
    service.validate_profile(alias)
    return alias, "created"


def _configure_delivery_interactive(series_path: Path, manifest_path: Path | None) -> dict[str, Any]:
    from .workstation import SeriesMetadata, update_series_publish_config
    service = CredentialService(manifest_path=manifest_path)
    initialized = service.initialize_manifest(
        namespace=_prompt_default(ui_text("凭据命名空间", "Credential namespace"), "main")
        if not service.manifest_path.exists() else "main",
    )
    current = SeriesMetadata.load(series_path)
    current_aliases = dict(current.publish.get("credential_aliases", {}))
    actions = {}
    aliases = {}
    for key, kind in (("r2", "r2"), ("ssh", "ssh"),
                      ("qbittorrent", "qbittorrent"), ("anibt", "anibt")):
        aliases[key], actions[key] = _choose_or_create_profile(
            service, kind, preferred_alias=current_aliases.get(key),
        )
    publish = dict(current.publish)
    resolved_ssh_alias, _ = service.resolve_ssh(aliases["ssh"])
    configured_ssh_alias = publish.get("ssh_alias")
    if configured_ssh_alias and configured_ssh_alias != resolved_ssh_alias:
        print(ui_text(
            f"检测到 SSH 配置不一致：bmlsub 凭据配置“{aliases['ssh']}”解析为 OpenSSH Host 别名“{resolved_ssh_alias}”，而番组当前保存的是“{configured_ssh_alias}”。将使用解析后的 OpenSSH Host 别名作为默认值。",
            f"SSH configuration mismatch: bmlsub credential profile '{aliases['ssh']}' resolves to OpenSSH Host alias '{resolved_ssh_alias}', while the series currently stores '{configured_ssh_alias}'. The resolved OpenSSH Host alias will be used as the default.",
        ), file=sys.stderr)
    values = {
        "r2_bucket": _prompt_default(ui_text("R2 存储桶", "R2 bucket"), str(publish.get("r2_bucket", "bml"))),
        "r2_access": _prompt_default(ui_text("R2 访问级别", "R2 access level"), str(publish.get("r2_access", "private"))),
        "rclone_remote": _prompt_default(ui_text("远程服务器 rclone remote 名称", "Remote server rclone remote name"), str(publish.get("rclone_remote", "r2"))),
        "ssh_alias": _prompt_default(ui_text(
            "OpenSSH Host 别名（来自所选 SSH 凭据配置）",
            "OpenSSH Host alias (from the selected SSH credential profile)",
        ), resolved_ssh_alias),
        "remote_root": _prompt_default(
            ui_text("VPS 宿主机平铺目录", "Flat directory on the VPS host"),
            str(publish.get("remote_root", "/data/dcapp/qb/downloads")),
        ),
        "qb_save_path": _prompt_default(
            ui_text("qBittorrent Docker 容器内对应目录", "Matching directory inside the qBittorrent Docker container"),
            str(publish.get("qb_save_path", "/downloads")),
        ),
        "qb_port": int(_prompt_default(ui_text("qBittorrent WebUI 端口", "qBittorrent WebUI port"), str(publish.get("qb_port", 8080)))),
        "qb_webui_origin": _prompt_default(
            ui_text("qBittorrent WebUI 来源地址", "qBittorrent WebUI origin"),
            str(publish.get("qb_webui_origin", "http://127.0.0.1:8080")),
        ),
    }
    summary = {
        "credential_manifest": initialized,
        "profiles": {key: {"alias": aliases[key], "action": actions[key]} for key in aliases},
        "series_publish": {**values, "credential_aliases": aliases},
    }
    print(ui_text("文件交付配置摘要：", "File delivery configuration summary:"), file=sys.stderr)
    print(json.dumps(summary, ensure_ascii=False, indent=2), file=sys.stderr)
    if not _confirm_stderr(ui_text(
        "保存以上非敏感番组配置和凭据引用",
        "Save the non-secret series configuration and credential references above",
    )):
        return {"status": "awaiting_confirmation", "setup": summary,
                "next_action": "confirm_delivery_configuration"}
    metadata = update_series_publish_config(
        series_path, values, credential_aliases=aliases,
    )
    return {"status": "succeeded", "setup": summary,
            "metadata_hash": metadata.content_hash, "publish": dict(metadata.publish)}


def _delivery_credential_status(config: Any) -> dict[str, Any]:
    service = CredentialService(manifest_path=config.credential_manifest)
    aliases = {
        "r2": config.r2_credential_profile,
        "ssh": config.ssh_profile,
        "qbittorrent": config.qb_credential_profile,
        "anibt": config.anibt_credential_profile,
    }
    profiles = []
    missing = []
    for kind, alias in aliases.items():
        if not alias:
            profiles.append({"kind": kind, "alias": None, "status": "missing"})
            missing.append(kind)
            continue
        try:
            result = service.validate_profile(alias)["profile"]
            profiles.append({"kind": kind, "alias": alias, "status": "available",
                             "reference": result.get("reference")})
        except Exception as exc:
            profiles.append({"kind": kind, "alias": alias, "status": "repair",
                             "error": str(exc)})
            missing.append(kind)
    return {
        "manifest": str(service.manifest_path),
        "status": "succeeded" if not missing else "needs_review",
        "profiles": profiles, "missing": missing,
    }


def _print_delivery_credentials(status: dict[str, Any]) -> None:
    print(ui_text("凭据与钥匙串检查：", "Credential and Keychain check:"), file=sys.stderr)
    print(ui_text(f"  凭据清单: {status['manifest']}", f"  Credential manifest: {status['manifest']}"), file=sys.stderr)
    for item in status["profiles"]:
        labels = {
            "available": ui_text("可复用", "reusable"),
            "repair": ui_text("需要修复", "repair required"),
            "missing": ui_text("需要新建", "creation required"),
        }
        print(f"  {item['kind']}: {item.get('alias') or '-'} ({labels[item['status']]})", file=sys.stderr)


def _print_publish_plan(plan: dict[str, Any], *, verbose: bool = False) -> None:
    deliveries = plan.get("deliveries", [])
    config = plan.get("config", {})
    print(ui_text("文件交付摘要：", "File delivery summary:"), file=sys.stderr)
    print(ui_text(f"  单集目录: {plan.get('episode_dir')}", f"  Episode directory: {plan.get('episode_dir')}"), file=sys.stderr)
    print(ui_text(
        f"  产品: {', '.join(item['product_key'] for item in deliveries)}",
        f"  Products: {', '.join(item['product_key'] for item in deliveries)}",
    ), file=sys.stderr)
    print(ui_text(
        f"  文件: {len(deliveries)} 个视频 + {len(deliveries)} 个 Torrent",
        f"  Files: {len(deliveries)} videos + {len(deliveries)} Torrents",
    ), file=sys.stderr)
    if deliveries:
        first_key = str(deliveries[0].get("r2_object_key", ""))
        print(f"  R2: {config.get('r2_bucket')}/{first_key.rsplit('/', 1)[0]}", file=sys.stderr)
    print(ui_text(f"  VPS 宿主机目录: {config.get('remote_dir')}", f"  VPS host directory: {config.get('remote_dir')}"), file=sys.stderr)
    print(ui_text(f"  qB 容器目录: {config.get('qb_save_path')}", f"  qB container directory: {config.get('qb_save_path')}"), file=sys.stderr)
    print(ui_text("  映射: 相同文件名通过 Docker volume 连接", "  Mapping: matching filenames are connected through a Docker volume"), file=sys.stderr)
    print(ui_text("  顺序: R2 → VPS 拉取 → qB 做种 → Anibt 发布", "  Order: R2 → VPS pull → qB seeding → Anibt publication"), file=sys.stderr)
    print(ui_text("  凭据配置: ", "  Credential profiles: "), end="", file=sys.stderr)
    print(f"R2={config.get('r2_credential_profile')}, "
          f"SSH={config.get('ssh_profile')}, qB={config.get('qb_credential_profile')}, "
          f"Anibt={config.get('anibt_credential_profile')}", file=sys.stderr)
    if verbose:
        print(ui_text("  详细文件映射:", "  Detailed file mapping:"), file=sys.stderr)
        for item in deliveries:
            print(f"    {item['product_key']}:", file=sys.stderr)
            print(ui_text(f"      视频: {item.get('content_path')}", f"      Video: {item.get('content_path')}"), file=sys.stderr)
            print(f"      Torrent: {item.get('torrent_path')}", file=sys.stderr)
            print(ui_text(f"      R2 视频: {item.get('r2_object_key')}", f"      R2 video: {item.get('r2_object_key')}"), file=sys.stderr)
            print(ui_text(f"      R2 种子: {item.get('r2_torrent_object_key')}", f"      R2 Torrent: {item.get('r2_torrent_object_key')}"), file=sys.stderr)
            print(ui_text(f"      VPS 视频: {item.get('remote_content_path')}", f"      VPS video: {item.get('remote_content_path')}"), file=sys.stderr)
            print(ui_text(f"      VPS 种子: {item.get('remote_torrent_path')}", f"      VPS Torrent: {item.get('remote_torrent_path')}"), file=sys.stderr)
    for item in plan.get("missing", []):
        print(ui_text(f"  缺失: {item}", f"  Missing: {item}"), file=sys.stderr)


def _workstation_start_delivery(args: argparse.Namespace) -> dict[str, Any]:
    _ensure_ui_language()
    from .workstation import (
        PublishConfig, WorkstationConfig, discover_episode_directories,
        discover_series_context, inspect_episode_stage,
        plan_publish, resolve_series_root, run_publish,
    )
    root = resolve_series_root(args.series_root)
    print(ui_text(f"番组根目录: {root}", f"Series root: {root}"), file=sys.stderr)
    episodes = discover_episode_directories(root)
    episode_id = args.episode_id
    if episode_id is None:
        if not sys.stdin.isatty():
            return {"status": "needs_review", "series_root": str(root),
                    "next_action": "provide_episode_id"}
        print(ui_text("可用单集目录:", "Available episode directories:"), file=sys.stderr)
        for index, item in enumerate(episodes, 1):
            print(f"  {index}. {item.name}  {item}", file=sys.stderr)
        selected = _prompt_stderr(ui_text("请选择序号或输入单集目录名: ", "Select a number or enter an episode directory name: "))
        episode_id = (episodes[int(selected) - 1].name
                      if selected.isdigit() and 1 <= int(selected) <= len(episodes)
                      else selected)
    inspection = inspect_episode_stage(root, episode_id)
    print(ui_text(f"本次文件交付目录: {inspection['episode_dir']}", f"File delivery episode directory: {inspection['episode_dir']}"), file=sys.stderr)
    if inspection.get("detected_phase") not in {"publish", "complete"}:
        return {
            "status": "needs_review", "inspection": inspection,
            "error": {
                "code": "local_production_incomplete",
                "message": "complete local production before starting file delivery",
            },
            "recommended_command": "bmlsub workstation start",
            "next_action": "complete_local_production",
        }
    def effective_publish_config(explicit: PublishConfig | None = None) -> PublishConfig:
        config = explicit or WorkstationConfig.from_series_context(
            discover_series_context(inspection["episode_dir"]),
        ).publish
        if args.credential_manifest is not None:
            config = replace(config, credential_manifest=args.credential_manifest)
        return config

    explicit_config = (PublishConfig(**_json_object(
        args.publish_config_json, "--publish-config-json",
    )) if args.publish_config_json is not None else None)
    publish_config = effective_publish_config(explicit_config)
    plan = plan_publish(
        inspection["episode_dir"], episode_id=inspection["episode_id"],
        publish_config=publish_config,
    )
    _print_publish_plan(plan, verbose=args.verbose_plan)
    credential_status = _delivery_credential_status(publish_config)
    _print_delivery_credentials(credential_status)
    configure = args.configure or (credential_status["status"] != "succeeded" and not args.yes)
    if not configure and plan["status"] != "succeeded" and sys.stdin.isatty():
        configure = _confirm_stderr(ui_text(
            "文件交付配置不完整，进入配置向导",
            "The file delivery configuration is incomplete. Open the configuration wizard",
        ))
    if args.yes and credential_status["status"] != "succeeded":
        return {"status": "needs_review", "inspection": inspection, "plan": plan,
                "credentials": credential_status,
                "next_action": "configure_delivery_credentials"}
    if configure:
        if not sys.stdin.isatty():
            return {"status": "needs_review", "inspection": inspection, "plan": plan,
                    "next_action": "run_configuration_in_tty"}
        setup = _configure_delivery_interactive(
            Path(inspection["metadata_path"]), args.credential_manifest,
        )
        if setup["status"] != "succeeded":
            return setup
        publish_config = effective_publish_config()
        plan = plan_publish(
            inspection["episode_dir"], episode_id=inspection["episode_id"],
            publish_config=publish_config,
        )
        _print_publish_plan(plan, verbose=args.verbose_plan)
        if plan["status"] != "succeeded":
            return {"status": "needs_review", "inspection": inspection,
                    "setup": setup, "plan": plan,
                    "next_action": "review_delivery_configuration"}
    elif plan["status"] != "succeeded":
        return {"status": "needs_review", "inspection": inspection, "plan": plan,
                "setup_requirements": {
                    "series_publish_fields": [
                        "remote_root", "qb_save_path", "ssh_alias", "credential_aliases.r2",
                        "credential_aliases.ssh", "credential_aliases.qbittorrent",
                        "credential_aliases.anibt",
                    ],
                    "secret_storage": "macos-keychain",
                    "credential_manifest": str(args.credential_manifest) if args.credential_manifest else None,
                },
                "next_action": "configure_delivery"}
    confirmed = args.yes or (args.execute and args.confirm_external_action)
    if not confirmed and sys.stdin.isatty():
        confirmed = _confirm_stderr(ui_text(
            "按 R2 → VPS → qB → Anibt 顺序开始交付",
            "Start delivery in R2 → VPS → qB → Anibt order",
        ))
    if not confirmed:
        return {
            "status": "awaiting_confirmation", "inspection": inspection,
            "plan": plan, "credentials": credential_status,
            "next_action": "confirm_external_action",
        }

    def confirm_item(stage: str, product_key: str) -> bool:
        if args.yes:
            return True
        labels = {
            "upload_r2": "R2 上传", "pull_remote": "VPS 远程拉取",
            "seed_qbittorrent": "qB 做种", "anibt": "Anibt 发布",
        }
        return _confirm_stderr(f"是否执行 {labels[stage]}: {product_key}")

    return run_publish(
        inspection["episode_dir"], episode_id=inspection["episode_id"],
        publish_config=publish_config, confirm_external_action=True, force=args.force,
        confirm_item=confirm_item,
    )


def _workstation_rebuild(args: argparse.Namespace) -> dict[str, Any]:
    _ensure_ui_language()
    from .workstation import (
        discover_episode_directories, plan_rebuild, resolve_series_root, run_rebuild,
        transcription_jobs_for_mode,
    )
    root = resolve_series_root(args.series_root)
    episode_id = args.episode_id
    episodes = discover_episode_directories(root)
    if episode_id is None:
        if not sys.stdin.isatty():
            return {"status": "needs_review", "series_root": str(root),
                    "next_action": "provide_episode_id"}
        print(ui_text("可用单集目录:", "Available episode directories:"), file=sys.stderr)
        for index, item in enumerate(episodes, 1):
            print(f"  {index}. {item.name}  {item}", file=sys.stderr)
        selected = _prompt_stderr(ui_text("请选择序号或输入单集目录名: ", "Select a number or enter an episode directory name: "))
        episode_id = (episodes[int(selected) - 1].name
                      if selected.isdigit() and 1 <= int(selected) <= len(episodes)
                      else selected)
    target = args.target
    if target is None and sys.stdin.isatty():
        print(ui_text("可重建范围:", "Available rebuild targets:"), file=sys.stderr)
        targets = (
            "preprocess", "delivery", "validate_subtitles_fonts", "encode_hevc",
            "encode_hardsub_chs", "encode_hardsub_cht", "mux_subtitles", "create_torrents",
        )
        for index, item in enumerate(targets, 1):
            print(f"  {index}. {item}", file=sys.stderr)
        selected = _prompt_stderr(ui_text("请选择序号或目标名称: ", "Select a number or enter a target name: "))
        target = (targets[int(selected) - 1]
                  if selected.isdigit() and 1 <= int(selected) <= len(targets)
                  else selected)
    plan = plan_rebuild(root, episode_id, target)
    if plan["status"] != "succeeded":
        return plan
    transcription = args.transcription
    if target == "preprocess":
        if transcription is None and sys.stdin.isatty():
            transcription = _prompt_transcription_mode()
        transcription = transcription or "full"
    confirmed = args.confirm_rebuild
    if not confirmed and sys.stdin.isatty():
        confirmed = _confirm_stderr(
            f"是否强制重建 {target}（保留历史，不执行发布）"
        )
    return run_rebuild(
        plan, confirmed=confirmed, source_video=args.source_video,
        production_subtitle=args.production_subtitle,
        reference_stream_index=args.reference_stream_index,
        audio_stream_index=args.audio_stream_index,
        whisper_jobs=transcription_jobs_for_mode(transcription) if transcription else (),
    )


def _workstation_series_retry_traditionalization(args: argparse.Namespace) -> dict[str, Any]:
    from .workstation import ensure_traditional_series_names, resolve_series_root
    root = resolve_series_root(args.series_root)
    result = ensure_traditional_series_names(
        root / "bgminfo" / "series.json", converter=args.converter,
        api_url=args.api_url, timeout=args.timeout,
    )
    return {
        "status": "succeeded" if result["status"] == "resolved" else "needs_review",
        **result,
        "next_action": None if result["status"] == "resolved" else "retry_traditionalization",
    }


def _workstation_series_show(args: argparse.Namespace) -> dict[str, Any]:
    from .workstation import discover_series_context
    context = discover_series_context(args.workspace)
    return {"status": "succeeded", "series": context.to_dict(),
            "metadata": context.metadata.to_dict()}


def _workstation_series_create(args: argparse.Namespace) -> dict[str, Any]:
    from .workstation import create_series_metadata, prompt_series_metadata
    if args.notes_file is not None and args.publish_json != "{}":
        raise ValueError("--notes-file cannot be combined with --publish-json")
    fields = (
        args.series_folder_name, args.title_chs, args.title_cht, args.romanized_title,
        args.group_chs, args.group_cht, args.bgm_id, args.anime_id,
    )
    if args.interactive:
        _ensure_ui_language()
        if any(value is not None for value in fields):
            raise ValueError("--interactive cannot be combined with series field arguments")
        if args.production_json != "{}" or args.publish_json != "{}" or args.notes_file is not None:
            raise ValueError("--interactive cannot be combined with profile or NOTE file arguments")
        metadata = prompt_series_metadata(
            parent_dir=args.parent_dir, replace=args.replace,
            input_fn=_prompt_stderr,
            output_fn=lambda message: print(message, file=sys.stderr),
            notes_fn=_prompt_markdown_notes,
        )
    else:
        required = {
            "series_folder_name": args.series_folder_name,
            "title_chs": args.title_chs,
            "romanized_title": args.romanized_title,
            "group_chs": args.group_chs,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            raise ValueError(f"series create requires: {', '.join(missing)}")
        publish = _json_object(args.publish_json, "--publish-json")
        notes = _read_notes_file(args.notes_file)
        if notes:
            publish["notes"] = notes
        metadata = create_series_metadata(
            args.series_folder_name, parent_dir=args.parent_dir,
            title_chs=args.title_chs, title_cht=args.title_cht,
            romanized_title=args.romanized_title,
            group_chs=args.group_chs, group_cht=args.group_cht,
            bgm_id=args.bgm_id, anime_id=args.anime_id,
            production=_json_object(args.production_json, "--production-json"),
            publish=publish,
            replace=args.replace,
        )
        if not metadata.title_cht or not metadata.group_cht:
            from .workstation import ensure_traditional_series_names
            traditional = ensure_traditional_series_names(
                metadata.path, converter=args.traditionalization_converter,
                api_url=args.traditionalization_api_url,
                timeout=args.traditionalization_timeout,
            )
            if traditional["status"] != "resolved":
                return {"status": "needs_review", **traditional,
                        "series_root": str(metadata.path.parent.parent),
                        "next_action": "retry_traditionalization"}
            from .workstation import SeriesMetadata
            metadata = SeriesMetadata.load(metadata.path)
    return {
        "status": "succeeded", "series_root": str(metadata.path.parent.parent),
        "metadata_path": str(metadata.path), "metadata_hash": metadata.content_hash,
        "metadata": metadata.to_dict(),
    }


def _workstation_preprocess(args: argparse.Namespace) -> dict[str, Any]:
    from .workstation import run_preprocess, transcription_jobs_for_mode
    return run_preprocess(
        args.workspace, episode_id=args.episode_id, source_video=args.source_video,
        reference_language=args.reference_language,
        reference_stream_index=args.reference_stream_index,
        audio_language=args.audio_language, audio_stream_index=args.audio_stream_index,
        whisper_jobs=transcription_jobs_for_mode(args.transcription),
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
        "hardsub_parameters": (_json_object(args.hardsub_parameters_json, "--hardsub-parameters-json")
                                if args.hardsub_parameters_json is not None else None),
        "hevc_parameters": (_json_object(args.hevc_parameters_json, "--hevc-parameters-json")
                             if args.hevc_parameters_json is not None else None),
        "ass_profile": (_json_object(args.ass_profile_json, "--ass-profile-json")
                        if args.ass_profile_json is not None else None),
        "torrent_profile": (_json_object(args.torrent_profile_json, "--torrent-profile-json")
                            if args.torrent_profile_json is not None else None),
        "force": args.force,
    }
    if args.step not in {"all", "delivery"}:
        return run_delivery_step(args.step, args.workspace, **kwargs)
    kwargs["selection"] = _delivery_selection_from_args(args)
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
