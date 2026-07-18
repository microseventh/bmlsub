"""Explicitly confirmed external publication orchestration."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any
import mimetypes

from .common import open_workstation
from .models import PublishConfig, WorkstationConfig
from .series import discover_series_context
from .state import load_manifest, pipeline_payload_step, refresh_summary, step_payload, update_manifest, write_step


PRODUCT_KEYS = (
    ("mp4_chs", "hardsub_chs_artifact_id"),
    ("mp4_cht", "hardsub_cht_artifact_id"),
    ("mkv_hevc", "muxed_mkv_artifact_id"),
)


def plan_publish(episode_dir: Path | str, *, episode_id: str | None = None,
                 publish_config: PublishConfig | None = None) -> dict[str, Any]:
    root = Path(episode_dir).expanduser().resolve()
    context = discover_series_context(root)
    identifier = episode_id or context.episode_id
    if identifier != context.episode_id:
        raise ValueError("episode_id does not match numeric episode directory")
    manifest = load_manifest(root)
    products = manifest.get("products", {})
    torrents = manifest.get("torrents", {})
    missing = []
    for key, manifest_key in PRODUCT_KEYS:
        if not products.get(manifest_key):
            missing.append(manifest_key)
        if not torrents.get(key):
            missing.append(f"torrent:{key}")
    config = publish_config or WorkstationConfig.from_series_context(context).publish
    configured = all((config.r2_credential_profile, config.ssh_profile,
                      config.qb_credential_profile, config.anibt_credential_profile,
                      config.remote_dir, config.ssh_alias or config.ssh_profile))
    if not configured:
        missing.append("publish_configuration")
    return {
        "schema_version": "workstation-plan-v1", "workflow_id": f"episode-{identifier}",
        "phase": "publish", "status": "failed" if missing else "succeeded",
        "episode_dir": str(root), "episode_id": identifier, "missing": missing,
        "products": products, "torrents": torrents, "config": config.to_dict(),
    }


def run_publish(episode_dir: Path | str, *, episode_id: str | None = None,
                publish_config: PublishConfig | None = None,
                confirm_external_action: bool = False, force: bool = False) -> dict[str, Any]:
    root = Path(episode_dir).expanduser().resolve()
    context = discover_series_context(root)
    identifier = episode_id or context.episode_id
    if identifier != context.episode_id:
        raise ValueError("episode_id does not match numeric episode directory")
    config = publish_config or WorkstationConfig.from_series_context(context).publish
    plan = plan_publish(root, episode_id=identifier, publish_config=config)
    if plan["status"] != "succeeded":
        payload = step_payload(
            workflow_id=plan["workflow_id"], phase="publish", step="publish.upload_r2",
            status="blocked", error={"code": "publish_not_ready", "message": "publish inputs are incomplete",
                                       "details": plan["missing"]},
            next_action="complete_delivery_and_publish_configuration",
        )
        write_step(root, payload)
        refresh_summary(root)
        return payload
    if not confirm_external_action:
        payload = step_payload(
            workflow_id=plan["workflow_id"], phase="publish", step="publish.upload_r2",
            status="awaiting_confirmation", next_action="confirm_external_action",
        )
        write_step(root, payload)
        refresh_summary(root)
        return payload
    workstation = open_workstation(WorkstationConfig.from_series_context(context, publish=config))
    manifest = load_manifest(root)
    receipt_ids: dict[str, dict[str, str]] = {"r2": {}, "remote": {}, "qb": {}, "anibt": {}}
    for product_key, product_manifest_key in PRODUCT_KEYS:
        content_id = manifest["products"][product_manifest_key]
        torrent_id = manifest["torrents"][product_key]
        for label, artifact_id in (("content", content_id), ("torrent", torrent_id)):
            artifact = workstation.store.get_artifact(artifact_id)
            profile = {
                "bucket": config.r2_bucket,
                "object_key": config.object_key(
                    identifier, artifact.path, series_folder_name=context.series_folder_name
                ),
                "content_type": mimetypes.guess_type(artifact.path.name)[0] or "application/octet-stream",
                "access": config.r2_access,
                "public_base_url": config.r2_public_base_url,
            }
            uploaded = workstation.pipeline.upload_r2(
                workspace=root, episode_id=identifier, artifact_id=artifact_id,
                profile={key: value for key, value in profile.items() if value is not None},
                credential_manifest=config.credential_manifest,
                credential_profile=config.r2_credential_profile, force=force,
            )
            pipeline_payload_step(root, workflow_id=workstation.config.workflow_id, phase="publish",
                                  step="publish.upload_r2", payload=uploaded)
            receipt_ids["r2"][f"{product_key}:{label}"] = uploaded["artifacts"][0]["artifact_id"]
        content = workstation.store.get_artifact(content_id)
        pulled = workstation.pipeline.pull_remote(
            workspace=root, episode_id=identifier, content_artifact_id=content_id,
            r2_receipt_artifact_id=receipt_ids["r2"][f"{product_key}:content"],
            profile={
                "ssh_alias": config.ssh_alias or config.ssh_profile,
                "rclone_remote": config.rclone_remote, "bucket": config.r2_bucket,
                "object_key": config.object_key(
                    identifier, content.path, series_folder_name=context.series_folder_name
                ),
                "target_path": config.remote_target(
                    content.path, series_folder_name=context.series_folder_name,
                    episode_id=identifier,
                ),
            }, connection_manifest=config.credential_manifest, ssh_profile=config.ssh_profile,
            force=force,
        )
        pipeline_payload_step(root, workflow_id=workstation.config.workflow_id, phase="publish",
                              step="publish.pull_remote", payload=pulled)
        remote_id = pulled["artifacts"][0]["artifact_id"]
        receipt_ids["remote"][product_key] = remote_id
        seeded = workstation.pipeline.seed_qbittorrent(
            workspace=root, episode_id=identifier, torrent_artifact_id=torrent_id,
            content_artifact_id=content_id, remote_content_artifact_id=remote_id,
            profile={
                "ssh_alias": config.ssh_alias or config.ssh_profile,
                "port": config.qb_port,
                "save_path": str(PurePosixPath(config.remote_dir) / context.series_folder_name / identifier),
                "webui_origin": config.qb_webui_origin,
            }, credential_manifest=config.credential_manifest,
            credential_profile=config.qb_credential_profile,
            connection_manifest=config.credential_manifest, ssh_profile=config.ssh_profile,
            force=force,
        )
        pipeline_payload_step(root, workflow_id=workstation.config.workflow_id, phase="publish",
                              step="publish.seed_qbittorrent", payload=seeded)
        receipt_ids["qb"][product_key] = seeded["artifacts"][0]["artifact_id"]
        profile = _anibt_profile(config, identifier, content.path, product_key)
        published = workstation.pipeline.publish_anibt(
            workspace=root, episode_id=identifier, torrent_artifact_id=torrent_id,
            profile=profile, credential_manifest=config.credential_manifest,
            credential_profile=config.anibt_credential_profile, force=force,
        )
        pipeline_payload_step(root, workflow_id=workstation.config.workflow_id, phase="publish",
                              step="publish.anibt", payload=published)
        receipt_ids["anibt"][product_key] = published["artifacts"][0]["artifact_id"]
    update_manifest(root, publish=receipt_ids)
    summary = refresh_summary(root)
    return {"status": summary["publish"]["status"], "receipts": receipt_ids,
            "manifest": load_manifest(root), "summary": summary}


def run_publish_step(step: str, episode_dir: Path | str, **kwargs) -> dict[str, Any]:
    result = run_publish(episode_dir, **kwargs)
    if step in {"all", "publish"}:
        return result
    from .state import load_status
    return load_status(episode_dir, step)


def _anibt_profile(config: PublishConfig, episode_id: str, path: Path, product_key: str) -> dict[str, Any]:
    values = {
        "mp4_chs": (["CHS", "JP"], "EMBEDDED", "MP4"),
        "mp4_cht": (["CHT", "JP"], "EMBEDDED", "MP4"),
        "mkv_hevc": (["CHS", "CHT", "JP"], "INTERNAL", "MKV"),
    }
    language, subtitle, format_name = values[product_key]
    return {
        "anime_id_type": "bgm", "anime_id": config.anime_id or str(config.bgm_id),
        "bgm_id": config.bgm_id, "title": path.stem, "episode_key": episode_id,
        "resolution": "1080p", "language": language, "subtitle": subtitle,
        "format": format_name, "file_size": path.stat().st_size, "notes": config.notes,
    }
