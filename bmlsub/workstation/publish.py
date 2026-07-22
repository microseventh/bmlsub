"""Explicitly confirmed external publication orchestration."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
import mimetypes

from .common import open_workstation
from ..state.models import ValidationStatus
from ..state.sqlite_store import SQLiteJobStore
from .models import PublishConfig, WorkstationConfig
from .series import discover_series_context
from .state import load_manifest, pipeline_payload_step, refresh_summary, step_payload, update_manifest, write_step


PRODUCT_KEYS = (
    ("mp4_chs", "hardsub_chs_artifact_id"),
    ("mp4_cht", "hardsub_cht_artifact_id"),
    ("mkv_hevc", "muxed_mkv_artifact_id"),
)


def plan_publish(episode_dir: Path | str, *, episode_id: str | None = None,
                 publish_config: PublishConfig | None = None,
                 publish_nyaa: bool = False) -> dict[str, Any]:
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
    deliveries = []
    store = SQLiteJobStore.for_workspace(root, root / "workstation" / "state")
    store.initialize()
    for key, manifest_key in PRODUCT_KEYS:
        content_id = products.get(manifest_key)
        torrent_id = torrents.get(key)
        content = store.get_artifact(content_id) if content_id else None
        torrent = store.get_artifact(torrent_id) if torrent_id else None
        if content_id and (content is None or content.validation_status is not ValidationStatus.VALID
                           or not content.path.is_file()):
            missing.append(f"artifact:{key}:content")
        if torrent_id and (torrent is None or torrent.validation_status is not ValidationStatus.VALID
                           or not torrent.path.is_file()):
            missing.append(f"artifact:{key}:torrent")
        if content is None:
            continue
        deliveries.append({
            "product_key": key,
            "content_artifact_id": content_id,
            "content_path": str(content.path),
            "content_size": content.size,
            "torrent_artifact_id": torrent_id,
            "torrent_path": str(torrent.path) if torrent else None,
            "torrent_size": torrent.size if torrent else None,
            "r2_object_key": config.object_key(
                identifier, content.path, series_folder_name=context.series_folder_name,
            ),
            "r2_torrent_object_key": (config.object_key(
                identifier, torrent.path, series_folder_name=context.series_folder_name,
            ) if torrent else None),
            "remote_content_path": (config.remote_target(content.path)
                                    if config.remote_dir else None),
            "remote_torrent_path": (config.remote_target(torrent.path)
                                    if config.remote_dir and torrent else None),
            "remote_target": (config.remote_target(content.path)
                              if config.remote_dir else None),
            "qb_save_path": (config.qb_save_path if config.remote_dir else None),
            "anibt": {
                "format": "MKV" if key == "mkv_hevc" else "MP4",
                "subtitle": "INTERNAL" if key == "mkv_hevc" else "EMBEDDED",
                "nyaa": publish_nyaa,
                "nyaa_category": "1_4" if publish_nyaa else None,
            },
        })
    return {
        "schema_version": "workstation-publish-plan-v1",
        "workflow_id": f"episode-{identifier}",
        "phase": "publish", "status": "failed" if missing else "succeeded",
        "episode_dir": str(root), "episode_id": identifier,
        "missing": list(dict.fromkeys(missing)),
        "products": products, "torrents": torrents, "config": config.to_dict(),
        "anibt": {"nyaa": publish_nyaa, "nyaa_category": "1_4" if publish_nyaa else None},
        "deliveries": deliveries,
        "external_actions": [
            "publish.upload_r2", "publish.pull_remote",
            "publish.seed_qbittorrent", "publish.anibt",
        ],
        "external_confirmation_required": True,
        "next_action": ("confirm_external_action" if not missing
                        else "complete_delivery_and_publish_configuration"),
    }


def _output_artifact_id(step: dict[str, Any], description: str) -> str:
    outputs = step.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        raise ValueError(f"{description} succeeded without a registered output Artifact")
    artifact_id = outputs[0].get("artifact_id")
    if not isinstance(artifact_id, str) or not artifact_id:
        raise ValueError(f"{description} output Artifact ID is missing")
    return artifact_id


def run_publish(episode_dir: Path | str, *, episode_id: str | None = None,
                publish_config: PublishConfig | None = None,
                confirm_external_action: bool = False, force: bool = False,
                confirm_item: Callable[[str, str], bool] | None = None,
                publish_nyaa: bool = False) -> dict[str, Any]:
    root = Path(episode_dir).expanduser().resolve()
    context = discover_series_context(root)
    identifier = episode_id or context.episode_id
    if identifier != context.episode_id:
        raise ValueError("episode_id does not match numeric episode directory")
    config = publish_config or WorkstationConfig.from_series_context(context).publish
    plan = plan_publish(
        root, episode_id=identifier, publish_config=config, publish_nyaa=publish_nyaa,
    )
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
    stored_publish = manifest.get("publish", {})
    receipt_ids: dict[str, dict[str, str]] = {
        key: dict(stored_publish.get(key, {})) for key in ("r2", "remote", "qb", "anibt")
    }

    def confirm(stage: str, product_key: str) -> dict[str, Any] | None:
        if confirm_item is None or confirm_item(stage, product_key):
            return None
        payload = step_payload(
            workflow_id=workstation.config.workflow_id, phase="publish",
            step=f"publish.{stage}", status="awaiting_confirmation",
            next_action=f"confirm_{stage}_{product_key}",
        )
        payload["product_key"] = product_key
        write_step(root, payload)
        refresh_summary(root)
        return payload

    products = []
    for product_key, product_manifest_key in PRODUCT_KEYS:
        content_id = manifest["products"][product_manifest_key]
        torrent_id = manifest["torrents"][product_key]
        products.append((
            product_key, content_id, torrent_id,
            workstation.store.get_artifact(content_id),
            workstation.store.get_artifact(torrent_id),
        ))

    for product_key, content_id, torrent_id, content, torrent in products:
        blocked = confirm("upload_r2", product_key)
        if blocked:
            return blocked
        for label, artifact in (("content", content), ("torrent", torrent)):
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
                workspace=root, episode_id=identifier, artifact_id=artifact.artifact_id,
                profile={key: value for key, value in profile.items() if value is not None},
                credential_manifest=config.credential_manifest,
                credential_profile=config.r2_credential_profile, force=force,
            )
            upload_step = pipeline_payload_step(
                root, workflow_id=workstation.config.workflow_id, phase="publish",
                step="publish.upload_r2", payload=uploaded,
            )
            if upload_step["status"] not in {"succeeded", "skipped"}:
                refresh_summary(root)
                return upload_step
            receipt_ids["r2"][f"{product_key}:{label}"] = _output_artifact_id(
                upload_step, "R2 upload",
            )
        update_manifest(root, publish=receipt_ids)

    for product_key, content_id, torrent_id, content, torrent in products:
        blocked = confirm("pull_remote", product_key)
        if blocked:
            return blocked
        for label, artifact in (("content", content), ("torrent", torrent)):
            pulled = workstation.pipeline.pull_remote(
                workspace=root, episode_id=identifier,
                content_artifact_id=artifact.artifact_id,
                r2_receipt_artifact_id=receipt_ids["r2"][f"{product_key}:{label}"],
                profile={
                    "ssh_alias": config.ssh_alias or config.ssh_profile,
                    "rclone_remote": config.rclone_remote, "bucket": config.r2_bucket,
                    "object_key": config.object_key(
                        identifier, artifact.path, series_folder_name=context.series_folder_name
                    ),
                    "target_path": config.remote_target(artifact.path),
                }, connection_manifest=config.credential_manifest,
                ssh_profile=config.ssh_profile, force=force,
            )
            pull_step = pipeline_payload_step(
                root, workflow_id=workstation.config.workflow_id, phase="publish",
                step="publish.pull_remote", payload=pulled,
            )
            if pull_step["status"] not in {"succeeded", "skipped"}:
                refresh_summary(root)
                return pull_step
            receipt_ids["remote"][f"{product_key}:{label}"] = _output_artifact_id(
                pull_step, f"remote {label} pull",
            )
        update_manifest(root, publish=receipt_ids)

    for product_key, content_id, torrent_id, content, torrent in products:
        blocked = confirm("seed_qbittorrent", product_key)
        if blocked:
            return blocked
        seeded = workstation.pipeline.seed_qbittorrent(
            workspace=root, episode_id=identifier, torrent_artifact_id=torrent_id,
            content_artifact_id=content_id,
            remote_content_artifact_id=receipt_ids["remote"][f"{product_key}:content"],
            remote_torrent_artifact_id=receipt_ids["remote"][f"{product_key}:torrent"],
            profile={
                "ssh_alias": config.ssh_alias or config.ssh_profile,
                "port": config.qb_port, "save_path": config.qb_save_path,
                "legacy_host_save_path": config.remote_save_path(),
                "webui_origin": config.qb_webui_origin,
            }, credential_manifest=config.credential_manifest,
            credential_profile=config.qb_credential_profile,
            connection_manifest=config.credential_manifest, ssh_profile=config.ssh_profile,
            force=force,
        )
        seed_step = pipeline_payload_step(
            root, workflow_id=workstation.config.workflow_id, phase="publish",
            step="publish.seed_qbittorrent", payload=seeded,
        )
        if seed_step["status"] not in {"succeeded", "skipped"}:
            refresh_summary(root)
            return seed_step
        receipt_ids["qb"][product_key] = _output_artifact_id(
            seed_step, "qBittorrent seeding",
        )
        update_manifest(root, publish=receipt_ids)

    for product_key, content_id, torrent_id, content, torrent in products:
        blocked = confirm("anibt", product_key)
        if blocked:
            return blocked
        published = workstation.pipeline.publish_anibt(
            workspace=root, episode_id=identifier, torrent_artifact_id=torrent_id,
            profile=_anibt_profile(
                config, identifier, content.path, product_key, publish_nyaa=publish_nyaa,
            ),
            credential_manifest=config.credential_manifest,
            credential_profile=config.anibt_credential_profile, force=force,
        )
        anibt_step = pipeline_payload_step(
            root, workflow_id=workstation.config.workflow_id, phase="publish",
            step="publish.anibt", payload=published,
        )
        if anibt_step["status"] not in {"succeeded", "skipped"}:
            refresh_summary(root)
            return anibt_step
        receipt_ids["anibt"][product_key] = _output_artifact_id(
            anibt_step, "Anibt publication",
        )
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


def _anibt_profile(config: PublishConfig, episode_id: str, path: Path, product_key: str,
                   *, publish_nyaa: bool = False) -> dict[str, Any]:
    values = {
        "mp4_chs": (["CHS", "JP"], "EMBEDDED", "MP4"),
        "mp4_cht": (["CHT", "JP"], "EMBEDDED", "MP4"),
        "mkv_hevc": (["CHS", "CHT", "JP"], "INTERNAL", "MKV"),
    }
    language, subtitle, format_name = values[product_key]
    profile = {
        "anime_id_type": "bgm", "anime_id": config.anime_id or str(config.bgm_id),
        "bgm_id": config.bgm_id, "title": path.stem, "episode_key": episode_id,
        "resolution": "1080p", "language": language, "subtitle": subtitle,
        "format": format_name, "file_size": path.stat().st_size, "notes": config.notes,
    }
    if publish_nyaa:
        profile.update({
            "trackers": [
                "https://tracker.anibt.net/announce",
                "http://nyaa.tracker.wf:7777/announce",
            ],
            "nyaa": True,
            "nyaa_category": "1_4",
            "nyaa_complete": False,
            "nyaa_remake": False,
        })
    return profile
