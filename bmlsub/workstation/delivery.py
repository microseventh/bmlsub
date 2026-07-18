"""Translation handoff validation and delivery production orchestration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..artifacts import ArtifactWriter
from ..execution.stage_runner import StageContext, StageOutcome, StageRunner
from ..state.fingerprints import fingerprint_file, fingerprint_parameters, fingerprint_tools, hash_json
from ..state.models import Diagnostic, StageInputBinding
from ..subtitle import SubtitleConversionOptions, run_subtitle_conversion
from .common import discover_production_subtitle, discover_source_video, ensure_directories, open_workstation, top_level_fonts
from .models import DeliveryConfig, WorkstationConfig
from .series import discover_series_context
from .naming import ProductKind
from .state import atomic_write_json, load_manifest, pipeline_payload_step, refresh_summary, result_step, step_payload, update_manifest, write_step


COPY_TOOL_VERSION = "workstation-copy-v1"


def plan_delivery(episode_dir: Path | str, *, episode_id: str | None = None,
                  production_subtitle: Path | str | None = None,
                  release_names=None) -> dict[str, Any]:
    root = Path(episode_dir).expanduser().resolve()
    context = discover_series_context(root)
    identifier = episode_id or context.episode_id
    if identifier != context.episode_id:
        raise ValueError("episode_id does not match numeric episode directory")
    release_names = release_names or WorkstationConfig.from_series_context(context).delivery.names
    manifest = load_manifest(root)
    reference = manifest.get("preprocess", {}).get("reference_delivery_path")
    references = (Path(reference),) if reference else ()
    video, video_error = discover_source_video(root)
    subtitle, subtitle_error = discover_production_subtitle(root, identifier, production_subtitle, references)
    fonts = top_level_fonts(root)
    errors = [item for item in (video_error, subtitle_error) if item]
    if not fonts:
        errors.append({"code": "input_missing", "message": "top-level Aegisub font package is missing"})
    needs_review = any(item["code"].endswith("ambiguous") for item in errors)
    return {
        "schema_version": "workstation-plan-v1", "workflow_id": f"episode-{identifier}",
        "phase": "delivery", "status": "needs_review" if needs_review else "failed" if errors else "succeeded",
        "episode_dir": str(root), "episode_id": identifier,
        "source_video": str(video) if video else None,
        "production_subtitle": str(subtitle) if subtitle else None,
        "fonts": [str(item) for item in fonts],
        "release_names": release_names.to_dict() if release_names else None,
        "errors": errors,
    }


def validate_translation_delivery(episode_dir: Path | str, *, episode_id: str | None = None,
                                  production_subtitle: Path | str | None = None,
                                  release_names=None, force: bool = False) -> dict[str, Any]:
    root = Path(episode_dir).expanduser().resolve()
    context = discover_series_context(root)
    identifier = episode_id or context.episode_id
    if identifier != context.episode_id:
        raise ValueError("episode_id does not match numeric episode directory")
    release_names = release_names or WorkstationConfig.from_series_context(context).delivery.names
    plan = plan_delivery(root, episode_id=identifier, production_subtitle=production_subtitle,
                         release_names=release_names)
    if plan["status"] != "succeeded":
        payload = step_payload(
            workflow_id=plan["workflow_id"], phase="translation",
            step="translation.validate_delivery", status=plan["status"],
            error={"code": "delivery_invalid", "message": "translation delivery is incomplete", "details": plan["errors"]},
            next_action="select_translation_inputs" if plan["status"] == "needs_review" else "provide_subtitle_and_fonts",
        )
        write_step(root, payload)
        refresh_summary(root)
        return payload
    if release_names is None:
        payload = step_payload(
            workflow_id=plan["workflow_id"], phase="translation",
            step="translation.validate_delivery", status="needs_review",
            error={"code": "release_names_missing", "message": "formal release names are required"},
            next_action="provide_release_names",
        )
        write_step(root, payload)
        refresh_summary(root)
        return payload
    base_config = WorkstationConfig.from_series_context(context)
    inherited = base_config.delivery
    config = WorkstationConfig.from_series_context(
        context,
        delivery=DeliveryConfig(
            names=release_names, production_subtitle=plan["production_subtitle"],
            hardsub_parameters=inherited.hardsub_parameters,
            hevc_parameters=inherited.hevc_parameters,
            ass_profile=inherited.ass_profile,
            torrent_profile=inherited.torrent_profile,
        ),
    )
    workstation = open_workstation(config)
    manifest = load_manifest(root)
    video_id = manifest.get("source", {}).get("video_artifact_id")
    if not video_id:
        registered = workstation.pipeline.register_video(
            Path(plan["source_video"]), workspace=root, episode_id=identifier,
            purposes=("source", "encode_source", "hardsub_source", "package_source"),
            default_for=("source", "encode_source", "hardsub_source", "package_source"), force=force,
        )
        video_id = registered["artifacts"][0]["artifact_id"]
        update_manifest(root, source={"video_artifact_id": video_id})
    subtitle_result = workstation.pipeline.register_subtitle(
        Path(plan["production_subtitle"]), workspace=root, episode_id=identifier,
        language="zh-hans", force=force,
    )
    subtitle_id = subtitle_result["artifacts"][0]["artifact_id"]
    font_ids = []
    for font in plan["fonts"]:
        registered = workstation.pipeline.register_font(
            Path(font), workspace=root, episode_id=identifier, force=force
        )
        font_ids.append(registered["artifacts"][0]["artifact_id"])
    update_manifest(root, subtitles={"chs_source_artifact_id": subtitle_id},
                    fonts={"artifact_ids": font_ids})
    payload = pipeline_payload_step(
        root, workflow_id=config.workflow_id, phase="translation",
        step="translation.validate_delivery", payload=subtitle_result,
    )
    atomic_write_json(root / "workstation" / "state" / "config.json", config.to_dict())
    refresh_summary(root)
    return payload


def _copy_stage(*, workstation, source_artifact_id: str, target: Path,
                artifact_type: str, language: str, stage_name: str,
                command_name: str, force: bool):
    source = workstation.store.get_artifact(source_artifact_id)
    if source is None:
        raise ValueError("source artifact is missing")
    input_fp = hash_json({"artifact_id": source.artifact_id, "hash": source.content_hash})
    parameter_fp = fingerprint_parameters({"target": str(target), "artifact_type": artifact_type, "language": language})
    tool_fp = fingerprint_tools({"copy": COPY_TOOL_VERSION})

    def adapter(context: StageContext) -> StageOutcome:
        written = ArtifactWriter(
            target, workspace=workstation.workspace, run_id=context.run_id,
            stage_id=context.stage_id, episode_id=workstation.config.episode_id,
            artifact_type=artifact_type, source_fingerprint=input_fp,
            parameter_fingerprint=parameter_fp,
            metadata={"language": language, "source_subtitle_artifact_id": source.artifact_id},
        ).write(lambda path: path.write_bytes(source.path.read_bytes()),
                lambda path: _validate_copy(source.path, path))
        return StageOutcome(artifacts=(written.artifact,), diagnostics=(Diagnostic(
            code="subtitle_copied", message="subtitle was transactionally copied and verified",
            context={"target": str(target)},
        ),))

    return StageRunner(workstation.store).run(
        workspace=workstation.workspace, command_name=command_name, stage_name=stage_name,
        episode_id=workstation.config.episode_id, input_fingerprint=input_fp,
        parameter_fingerprint=parameter_fp, tool_fingerprint=tool_fp, adapter=adapter,
        inputs=(StageInputBinding(source.artifact_id, "subtitle", 0),), force=force,
    )


def run_delivery(episode_dir: Path | str, *, episode_id: str | None = None,
                 production_subtitle: Path | str | None = None, release_names=None,
                 hardsub_parameters: dict[str, Any] | None = None,
                 hevc_parameters: dict[str, Any] | None = None,
                 ass_profile: dict[str, Any] | None = None,
                 torrent_profile: dict[str, Any] | None = None,
                 converter: str = "Taiwan", conversion_api_url: str = "https://api.zhconvert.org/convert",
                 conversion_timeout: int = 60, force: bool = False) -> dict[str, Any]:
    root = Path(episode_dir).expanduser().resolve()
    context = discover_series_context(root)
    identifier = episode_id or context.episode_id
    if identifier != context.episode_id:
        raise ValueError("episode_id does not match numeric episode directory")
    release_names = release_names or WorkstationConfig.from_series_context(context).delivery.names
    validated = validate_translation_delivery(
        root, episode_id=identifier, production_subtitle=production_subtitle,
        release_names=release_names, force=force,
    )
    if validated["status"] not in {"succeeded", "skipped"}:
        return validated
    base_config = WorkstationConfig.from_series_context(context)
    inherited = base_config.delivery
    config = WorkstationConfig.from_series_context(
        context,
        delivery=DeliveryConfig(
            names=release_names, production_subtitle=production_subtitle,
            hardsub_parameters=(hardsub_parameters if hardsub_parameters is not None else inherited.hardsub_parameters),
            hevc_parameters=(hevc_parameters if hevc_parameters is not None else inherited.hevc_parameters),
            ass_profile=(ass_profile if ass_profile is not None else inherited.ass_profile),
            torrent_profile=(torrent_profile if torrent_profile is not None else inherited.torrent_profile),
        ),
    )
    workstation = open_workstation(config)
    paths = ensure_directories(root)
    manifest = load_manifest(root)
    source_video_id = manifest["source"]["video_artifact_id"]
    chs_source_id = manifest["subtitles"]["chs_source_artifact_id"]
    font_ids = tuple(manifest["fonts"]["artifact_ids"])

    snapshot = _copy_stage(
        workstation=workstation, source_artifact_id=chs_source_id,
        target=paths["subtitles"] / f"{identifier}.chs&jpn.ass",
        artifact_type="workstation.subtitle.chs", language="zh-hans",
        stage_name="workstation.snapshot_chs_subtitle",
        command_name="workstation.delivery.snapshot-chs", force=force,
    )
    snapshot_step = result_step(root, workflow_id=config.workflow_id, phase="delivery",
                                step="delivery.snapshot_chs_subtitle", result=snapshot)
    if snapshot_step["status"] not in {"succeeded", "skipped"}:
        refresh_summary(root)
        return snapshot_step
    chs_snapshot_id = snapshot_step["outputs"][0]["artifact_id"]
    update_manifest(root, subtitles={"chs_snapshot_artifact_id": chs_snapshot_id})

    chs_artifact = workstation.store.get_artifact(chs_snapshot_id)
    cht_target = paths["subtitles"] / f"{identifier}.cht&jpn.ass"
    conversion = run_subtitle_conversion(
        chs_artifact.path, cht_target, workspace=root, episode_id=identifier,
        options=SubtitleConversionOptions(converter=converter, api_url=conversion_api_url,
                                          timeout=conversion_timeout),
        store=workstation.store, state_dir=config.state_dir,
        source_artifact_id=chs_snapshot_id, artifact_type="workstation.subtitle.cht",
        language="zh-hant", force=force,
    )
    conversion_step = result_step(root, workflow_id=config.workflow_id, phase="delivery",
                                  step="delivery.generate_cht_subtitle", result=conversion,
                                  inputs=(chs_artifact,))
    if conversion_step["status"] not in {"succeeded", "skipped"}:
        refresh_summary(root)
        return conversion_step
    cht_id = conversion_step["outputs"][0]["artifact_id"]
    update_manifest(root, subtitles={"cht_workstation_artifact_id": cht_id})

    top_cht = root / f"{identifier}.cht&jpn.ass"
    cht_artifact = workstation.store.get_artifact(cht_id)
    if top_cht.exists() and fingerprint_file(top_cht).content_hash != cht_artifact.content_hash:
        payload = step_payload(
            workflow_id=config.workflow_id, phase="delivery", step="delivery.publish_cht_subtitle",
            status="needs_review", inputs=(cht_artifact,),
            error={"code": "cht_conflict", "message": "top-level and workstation CHT subtitles differ"},
            next_action="choose_manual_or_regenerated_cht",
        )
        write_step(root, payload)
        refresh_summary(root)
        return payload
    published = _copy_stage(
        workstation=workstation, source_artifact_id=cht_id, target=top_cht,
        artifact_type="workstation.subtitle.delivery.cht", language="zh-hant",
        stage_name="workstation.publish_cht_subtitle",
        command_name="workstation.delivery.publish-cht", force=force,
    )
    publish_step = result_step(root, workflow_id=config.workflow_id, phase="delivery",
                               step="delivery.publish_cht_subtitle", result=published,
                               inputs=(cht_artifact,))
    cht_delivery_id = publish_step["outputs"][0]["artifact_id"]
    update_manifest(root, subtitles={"cht_delivery_artifact_id": cht_delivery_id})

    analyses = []
    for key, subtitle_id in (("chs", chs_snapshot_id), ("cht", cht_id)):
        analysis = workstation.pipeline.analyze_ass(
            workspace=root, episode_id=identifier, subtitle_artifact_id=subtitle_id,
            video_artifact_id=source_video_id, font_artifact_ids=font_ids,
            profile=ass_profile or {}, output=paths["subtitle_analysis"] / f"{key}.analysis.json",
            force=force,
        )
        analyses.append(analysis)
    _font_report(paths["subtitle_analysis"] / "font-report.json", analyses)
    validation_payload = {
        "status": "succeeded",
        "run_id": analyses[-1].get("run_id"), "stage_name": "subtitle.analyze_ass",
        "diagnostics": [{
            "code": "aegisub_font_package_registered",
            "message": "ASS diagnostics were recorded without blocking production; font validation belongs to Aegisub",
        }],
        "needs_review": False,
    }
    pipeline_payload_step(
        root, workflow_id=config.workflow_id, phase="delivery",
        step="delivery.validate_subtitles_fonts", payload=validation_payload,
        status="succeeded",
    )

    requests = {}
    executions = {}
    products = config.product_paths()
    request_specs = (
        ("hevc", "encode", source_video_id, (), "hevc-10bit", config.intermediate_path(), config.delivery.hevc_parameters),
        ("hardsub_chs", "hardsub", source_video_id, (chs_snapshot_id,), "h264-chs", products[ProductKind.MP4_CHS.value], config.delivery.hardsub_parameters),
        ("hardsub_cht", "hardsub", source_video_id, (cht_id,), "h264-cht", products[ProductKind.MP4_CHT.value], config.delivery.hardsub_parameters),
    )
    for key, operation, video_id, subtitle_ids, profile, target, parameters in request_specs:
        created = workstation.pipeline.create_production_request(
            workspace=root, episode_id=identifier, operation=operation,
            video_artifact_id=video_id,
            subtitle_artifact_id=subtitle_ids[0] if operation == "hardsub" else None,
            font_artifact_ids=font_ids if operation == "hardsub" else (),
            output_profile=profile, output_target=target, parameters=parameters,
        )
        requests[key] = created["request"]
        executions[key] = workstation.pipeline.execute_production_request(
            created["request"]["request_id"], workspace=root, force=force
        )
        pipeline_payload_step(
            root, workflow_id=config.workflow_id, phase="delivery",
            step=f"delivery.encode_{key}", payload=executions[key],
        )
        if executions[key]["status"] not in {"succeeded", "skipped"}:
            refresh_summary(root)
            return executions[key]
    hevc_id = executions["hevc"]["artifacts"][0]["artifact_id"]
    mux = workstation.pipeline.create_production_request(
        workspace=root, episode_id=identifier, operation="mux_subtitle",
        video_artifact_id=hevc_id, subtitle_artifact_ids=(chs_snapshot_id, cht_id),
        font_artifact_ids=font_ids, output_profile="mkv-subtitle",
        output_target=products[ProductKind.MKV_HEVC.value],
        parameters={"default_subtitle_ordinal": 0},
    )
    mux_result = workstation.pipeline.execute_production_request(
        mux["request"]["request_id"], workspace=root, force=force
    )
    pipeline_payload_step(root, workflow_id=config.workflow_id, phase="delivery",
                          step="delivery.mux_subtitles", payload=mux_result)
    product_ids = {
        ProductKind.MP4_CHS.value: executions["hardsub_chs"]["artifacts"][0]["artifact_id"],
        ProductKind.MP4_CHT.value: executions["hardsub_cht"]["artifacts"][0]["artifact_id"],
        ProductKind.MKV_HEVC.value: mux_result["artifacts"][0]["artifact_id"],
    }
    update_manifest(root, products={
        "hardsub_chs_artifact_id": product_ids[ProductKind.MP4_CHS.value],
        "hardsub_cht_artifact_id": product_ids[ProductKind.MP4_CHT.value],
        "muxed_mkv_artifact_id": product_ids[ProductKind.MKV_HEVC.value],
        "hevc_intermediate_artifact_id": hevc_id,
    })
    torrent_ids = {}
    for key, product_id in product_ids.items():
        torrent = workstation.pipeline.create_torrent(
            workspace=root, episode_id=identifier, content_artifact_id=product_id,
            profile=torrent_profile or {"format": "v1"}, output=config.torrent_paths()[key],
            force=force,
        )
        torrent_ids[key] = torrent["artifacts"][0]["artifact_id"]
    pipeline_payload_step(root, workflow_id=config.workflow_id, phase="delivery",
                          step="delivery.create_torrents", payload=torrent)
    update_manifest(root, torrents=torrent_ids)
    summary = refresh_summary(root)
    return {"status": summary["delivery"]["status"], "manifest": load_manifest(root),
            "summary": summary, "products": product_ids, "torrents": torrent_ids}


def run_delivery_step(step: str, episode_dir: Path | str, *, episode_id: str | None = None,
                      production_subtitle: Path | str | None = None, release_names=None,
                      hardsub_parameters: dict[str, Any] | None = None,
                      hevc_parameters: dict[str, Any] | None = None,
                      ass_profile: dict[str, Any] | None = None,
                      torrent_profile: dict[str, Any] | None = None,
                      force: bool = False) -> dict[str, Any]:
    if step in {"all", "delivery"}:
        return run_delivery(
            episode_dir, episode_id=episode_id, production_subtitle=production_subtitle,
            release_names=release_names, hardsub_parameters=hardsub_parameters,
            hevc_parameters=hevc_parameters, ass_profile=ass_profile,
            torrent_profile=torrent_profile, force=force,
        )
    root = Path(episode_dir).expanduser().resolve()
    context = discover_series_context(root)
    identifier = episode_id or context.episode_id
    if identifier != context.episode_id:
        raise ValueError("episode_id does not match numeric episode directory")
    base_config = WorkstationConfig.from_series_context(context)
    inherited = base_config.delivery
    names = release_names or inherited.names
    config = WorkstationConfig.from_series_context(
        context,
        delivery=DeliveryConfig(
            names=names, production_subtitle=production_subtitle,
            hardsub_parameters=(hardsub_parameters if hardsub_parameters is not None else inherited.hardsub_parameters),
            hevc_parameters=(hevc_parameters if hevc_parameters is not None else inherited.hevc_parameters),
            ass_profile=(ass_profile if ass_profile is not None else inherited.ass_profile),
            torrent_profile=(torrent_profile if torrent_profile is not None else inherited.torrent_profile),
        ),
    )
    workstation = open_workstation(config)
    atomic_write_json(root / "workstation" / "state" / "config.json", config.to_dict())
    manifest = load_manifest(root)
    products = config.product_paths()

    if step == "validate_subtitles_fonts":
        font_report = root / "workstation" / "delivery" / "subtitle-analysis" / "font-report.json"
        if not font_report.is_file():
            return step_payload(
                workflow_id=config.workflow_id, phase="delivery",
                step="delivery.validate_subtitles_fonts", status="failed",
                error={"code": "input_missing", "message": "font diagnostics report is missing"},
                next_action="run_subtitle_analysis",
            )
        payload = {
            "status": "succeeded", "run_id": None, "stage_name": "subtitle.analyze_ass",
            "diagnostics": [{
                "code": "aegisub_font_package_registered",
                "message": "ASS diagnostics were recorded without blocking production; font validation belongs to Aegisub",
            }],
            "needs_review": False,
        }
        result = pipeline_payload_step(
            root, workflow_id=config.workflow_id, phase="delivery",
            step="delivery.validate_subtitles_fonts", payload=payload, status="succeeded",
        )
        refresh_summary(root)
        return result

    source_video_id = manifest.get("source", {}).get("video_artifact_id")
    subtitle_ids = manifest.get("subtitles", {})
    font_ids = tuple(manifest.get("fonts", {}).get("artifact_ids", ()))
    if not source_video_id:
        raise ValueError("source video artifact is missing from workstation manifest")

    if step == "encode_hevc":
        created = workstation.pipeline.create_production_request(
            workspace=root, episode_id=identifier, operation="encode",
            video_artifact_id=source_video_id, output_profile="hevc-10bit",
            output_target=config.intermediate_path(), parameters=config.delivery.hevc_parameters,
        )
        payload = workstation.pipeline.execute_production_request(
            created["request"]["request_id"], workspace=root, force=force,
        )
        result = pipeline_payload_step(
            root, workflow_id=config.workflow_id, phase="delivery",
            step="delivery.encode_hevc", payload=payload,
        )
        if result["status"] in {"succeeded", "skipped"} and result["outputs"]:
            update_manifest(root, products={
                "hevc_intermediate_artifact_id": result["outputs"][0]["artifact_id"],
            })
        refresh_summary(root)
        return result

    if step in {"encode_hardsub_chs", "encode_hardsub_cht"}:
        language_key = "chs" if step.endswith("chs") else "cht"
        subtitle_key = ("chs_snapshot_artifact_id" if language_key == "chs"
                        else "cht_workstation_artifact_id")
        subtitle_id = subtitle_ids.get(subtitle_key)
        if not subtitle_id:
            raise ValueError(f"{language_key} subtitle artifact is missing from workstation manifest")
        product_kind = ProductKind.MP4_CHS.value if language_key == "chs" else ProductKind.MP4_CHT.value
        created = workstation.pipeline.create_production_request(
            workspace=root, episode_id=identifier, operation="hardsub",
            video_artifact_id=source_video_id, subtitle_artifact_id=subtitle_id,
            font_artifact_ids=font_ids, output_profile=f"h264-{language_key}",
            output_target=products[product_kind], parameters=config.delivery.hardsub_parameters,
        )
        payload = workstation.pipeline.execute_production_request(
            created["request"]["request_id"], workspace=root, force=force,
        )
        result = pipeline_payload_step(
            root, workflow_id=config.workflow_id, phase="delivery",
            step=f"delivery.encode_hardsub_{language_key}", payload=payload,
        )
        if result["status"] in {"succeeded", "skipped"} and result["outputs"]:
            update_manifest(root, products={
                f"hardsub_{language_key}_artifact_id": result["outputs"][0]["artifact_id"],
            })
        refresh_summary(root)
        return result

    if step == "mux_subtitles":
        hevc_id = manifest.get("products", {}).get("hevc_intermediate_artifact_id")
        chs_id = subtitle_ids.get("chs_snapshot_artifact_id")
        cht_id = subtitle_ids.get("cht_workstation_artifact_id")
        if not all((hevc_id, chs_id, cht_id)):
            raise ValueError("HEVC intermediate and both subtitle artifacts must be recorded before muxing")
        created = workstation.pipeline.create_production_request(
            workspace=root, episode_id=identifier, operation="mux_subtitle",
            video_artifact_id=hevc_id, subtitle_artifact_ids=(chs_id, cht_id),
            font_artifact_ids=font_ids, output_profile="mkv-subtitle",
            output_target=products[ProductKind.MKV_HEVC.value],
            parameters={"default_subtitle_ordinal": 0},
        )
        payload = workstation.pipeline.execute_production_request(
            created["request"]["request_id"], workspace=root, force=force,
        )
        result = pipeline_payload_step(
            root, workflow_id=config.workflow_id, phase="delivery",
            step="delivery.mux_subtitles", payload=payload,
        )
        if result["status"] in {"succeeded", "skipped"} and result["outputs"]:
            update_manifest(root, products={
                "muxed_mkv_artifact_id": result["outputs"][0]["artifact_id"],
            })
        refresh_summary(root)
        return result

    if step == "create_torrents":
        product_ids = {
            ProductKind.MP4_CHS.value: manifest.get("products", {}).get("hardsub_chs_artifact_id"),
            ProductKind.MP4_CHT.value: manifest.get("products", {}).get("hardsub_cht_artifact_id"),
            ProductKind.MKV_HEVC.value: manifest.get("products", {}).get("muxed_mkv_artifact_id"),
        }
        missing = [key for key, value in product_ids.items() if not value]
        if missing:
            raise ValueError(f"product artifacts are missing before torrent creation: {', '.join(missing)}")
        torrent_ids = {}
        last_payload = None
        for key, product_id in product_ids.items():
            last_payload = workstation.pipeline.create_torrent(
                workspace=root, episode_id=identifier, content_artifact_id=product_id,
                profile=config.delivery.torrent_profile, output=config.torrent_paths()[key],
                force=force,
            )
            torrent_ids[key] = last_payload["artifacts"][0]["artifact_id"]
        result = pipeline_payload_step(
            root, workflow_id=config.workflow_id, phase="delivery",
            step="delivery.create_torrents", payload=last_payload,
        )
        update_manifest(root, torrents=torrent_ids)
        refresh_summary(root)
        return result

    raise ValueError(f"unsupported delivery step: {step}")


def _validate_copy(source: Path, target: Path) -> None:
    if fingerprint_file(source).content_hash != fingerprint_file(target).content_hash:
        raise ValueError("subtitle copy hash mismatch")


def _font_report(target: Path, analyses: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {}
    for payload in analyses:
        for artifact in payload.get("artifacts", []):
            for key, value in artifact.get("metadata", {}).get("font_status_counts", {}).items():
                counts[key] = counts.get(key, 0) + int(value)
    report = {
        "schema_version": "workstation-font-report-v1",
        "authoritative": False,
        "blocking": False,
        "validation_owner": "aegisub",
        "status_counts": counts,
    }
    atomic_write_json(target, report)
    return report
