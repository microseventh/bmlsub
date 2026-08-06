"""Human-readable final summaries for the workstation quick mode."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
from typing import Any, Literal


Language = Literal["zh", "en"]

_STEP_LABELS = {
    "preprocess.inspect_video": ("检查并登记源视频", "Inspect and register source video"),
    "preprocess.extract_reference_subtitles": ("提取英语参考字幕", "Extract English reference subtitles"),
    "preprocess.publish_reference_subtitles": ("发布英语参考字幕副本", "Publish English reference subtitle copies"),
    "preprocess.extract_audio": ("提取归档与转录音频", "Extract archive and transcription audio"),
    "preprocess.transcribe.quick": ("快速转录", "Quick transcription"),
    "preprocess.transcribe.direct": ("整段转录", "Direct transcription"),
    "preprocess.transcribe.chunked": ("分段转录", "Chunked transcription"),
    "preprocess.export_transcript_text.quick": ("导出快速转录文本", "Export quick transcript text"),
    "preprocess.export_transcript_text.direct": ("导出整段转录文本", "Export direct transcript text"),
    "preprocess.export_transcript_text.chunked": ("导出分段转录文本", "Export chunked transcript text"),
    "translation.validate_delivery": ("登记正式字幕", "Register production subtitles"),
    "delivery.snapshot_chs_subtitle": ("登记正式 CHS 字幕", "Register production CHS subtitle"),
    "delivery.snapshot_cht_subtitle": ("登记正式 CHT 字幕", "Register production CHT subtitle"),
    "delivery.generate_cht_subtitle": ("生成 CHT 字幕", "Generate CHT subtitle"),
    "delivery.publish_cht_subtitle": ("发布 CHT 字幕工作副本", "Publish CHT subtitle working copy"),
    "delivery.validate_subtitles_fonts": ("检查字幕与字体", "Validate subtitles and fonts"),
    "delivery.encode_hevc": ("HEVC 10-bit 编码", "HEVC 10-bit encoding"),
    "delivery.encode_hardsub_chs": ("简体 MP4 压制", "CHS MP4 encoding"),
    "delivery.encode_hardsub_cht": ("繁体 MP4 压制", "CHT MP4 encoding"),
    "delivery.mux_subtitles": ("简繁内封 MKV", "Bilingual subtitle MKV mux"),
    "delivery.create_torrents": ("创建 Torrent", "Create Torrents"),
    "publish.upload_r2": ("上传 R2", "Upload to R2"),
    "publish.pull_remote": ("同步到 VPS", "Sync to VPS"),
    "publish.seed_qbittorrent": ("qBittorrent 做种", "Seed with qBittorrent"),
    "publish.anibt": ("发布 Anibt", "Publish to Anibt"),
}

_PRODUCT_LABELS = {
    "mp4_chs": ("简体 MP4", "CHS MP4"),
    "mp4_cht": ("繁体 MP4", "CHT MP4"),
    "mkv_hevc": ("简繁内封 MKV", "Bilingual HEVC MKV"),
}

_ACTION_LABELS = {
    "provide_episode_id": ("指定要处理的单集", "Provide an episode ID"),
    "create_series_metadata": ("创建番组元数据", "Create series metadata"),
    "retry_traditionalization": ("重试繁体名称转换", "Retry Traditional Chinese name conversion"),
    "create_numeric_episode_directory": ("创建数字单集目录", "Create a numeric episode directory"),
    "run_preprocess": ("确认并执行预处理", "Confirm and run preprocessing"),
    "resume_preprocess": ("恢复未完成的预处理", "Resume preprocessing"),
    "run_delivery": ("确认并执行本地压制", "Confirm and run local production"),
    "confirm_delivery_plan": ("确认本地压制方案", "Confirm the local production plan"),
    "start_delivery": ("开始外部文件交付", "Start external file delivery"),
    "configure_delivery": ("配置外部文件交付", "Configure external file delivery"),
    "configure_delivery_credentials": ("配置文件交付凭据", "Configure file delivery credentials"),
    "run_configuration_in_tty": ("在交互终端中完成文件交付配置", "Configure file delivery in an interactive terminal"),
    "confirm_external_action": ("确认 R2、VPS、qB 和 Anibt 外部操作", "Confirm R2, VPS, qB, and Anibt actions"),
    "complete_local_production": ("先完成本地压制", "Complete local production first"),
    "complete_template_and_save_as_bgminfo/series.json": (
        "填写模板并另存为 bgminfo/series.json",
        "Complete the template and save it as bgminfo/series.json",
    ),
    "complete_template_and_rerun": (
        "填写模板后重新运行 bmlsub ws start",
        "Complete the template and rerun bmlsub ws start",
    ),
}

_SUCCESS = {"succeeded", "success", "complete", "completed"}
_REUSED = {"skipped", "reused"}
_FAILED = {"failed", "error"}
_PENDING = {"pending", "blocked", "needs_review", "awaiting_confirmation", "running", "partial"}


def _text(language: Language, zh: str, en: str) -> str:
    return zh if language == "zh" else en


def _localized(mapping: Mapping[str, tuple[str, str]], key: str, language: Language) -> str:
    value = mapping.get(key)
    return _text(language, *value) if value else key


def _status_category(status: Any, *, reused: bool = False) -> str | None:
    normalized = str(status or "").lower()
    if reused or normalized in _REUSED:
        return "reused"
    if normalized in _SUCCESS:
        return "completed"
    if normalized in _FAILED:
        return "failed"
    if normalized in _PENDING:
        return "pending"
    return None


def _status_label(status: Any, language: Language) -> str:
    labels = {
        "succeeded": ("成功", "Succeeded"),
        "partial": ("部分完成", "Partially completed"),
        "awaiting_confirmation": ("等待确认", "Awaiting confirmation"),
        "needs_review": ("需要处理", "Needs review"),
        "failed": ("失败", "Failed"),
        "blocked": ("已阻断", "Blocked"),
        "pending": ("待处理", "Pending"),
        "running": ("执行中", "Running"),
    }
    value = labels.get(str(status or ""))
    return _text(language, *value) if value else str(status or _text(language, "未知", "Unknown"))


def _first_mapping_value(payload: Mapping[str, Any], key: str) -> Any:
    value = payload.get(key)
    if value not in (None, "", [], {}):
        return value
    for nested_key in ("inspection", "plan"):
        nested = payload.get(nested_key)
        if isinstance(nested, Mapping):
            value = nested.get(key)
            if value not in (None, "", [], {}):
                return value
    return None


def _iter_step_payloads(value: Any):
    if isinstance(value, Mapping):
        if isinstance(value.get("step"), str) and value.get("status") is not None:
            yield value
        for nested in value.values():
            yield from _iter_step_payloads(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            yield from _iter_step_payloads(nested)


def _phase_steps(payload: Mapping[str, Any]):
    summary = payload.get("summary")
    if not isinstance(summary, Mapping):
        return
    for phase in ("preprocess", "translation", "delivery", "publish"):
        phase_payload = summary.get(phase)
        if not isinstance(phase_payload, Mapping):
            continue
        steps = phase_payload.get("steps")
        if isinstance(steps, Mapping):
            yield from steps.items()


def _error_message(error: Any) -> str | None:
    if isinstance(error, Mapping):
        message = error.get("message")
        return str(message) if message else None
    return str(error) if error else None


def _collect_items(payload: Mapping[str, Any], language: Language) -> dict[str, list[str]]:
    records: OrderedDict[str, tuple[str, str]] = OrderedDict()

    def record(key: str, category: str | None, label: str) -> None:
        if category is None:
            return
        records.pop(key, None)
        records[key] = (category, label)

    for step, status in _phase_steps(payload) or ():
        record(f"step:{step}", _status_category(status), _localized(_STEP_LABELS, str(step), language))

    for step_payload in _iter_step_payloads(payload):
        step = str(step_payload["step"])
        label = _localized(_STEP_LABELS, step, language)
        error = _error_message(step_payload.get("error"))
        if error:
            label = f"{label}: {error}"
        record(
            f"step:{step}",
            _status_category(step_payload.get("status"), reused=bool(step_payload.get("reused"))),
            label,
        )

    for key in payload.get("completed_products") or ():
        name = str(key)
        record(f"product:{name}", "completed", _localized(_PRODUCT_LABELS, name, language))
    for key in payload.get("deferred_products") or ():
        name = str(key)
        record(f"product:{name}", "pending", _localized(_PRODUCT_LABELS, name, language))

    overall = str(payload.get("status") or "")
    if overall in _PENDING | _FAILED:
        plan = payload.get("plan")
        if isinstance(plan, Mapping):
            planned_steps = plan.get("steps") or plan.get("external_actions") or ()
            if isinstance(planned_steps, (list, tuple)):
                for step in planned_steps:
                    name = str(step)
                    if f"step:{name}" not in records:
                        record(f"step:{name}", "pending", _localized(_STEP_LABELS, name, language))

    for container in (payload, payload.get("inspection"), payload.get("plan")):
        if not isinstance(container, Mapping):
            continue
        for blocker in container.get("blocking") or ():
            if not isinstance(blocker, Mapping):
                continue
            code = str(blocker.get("code") or blocker.get("message") or "blocker")
            message = str(blocker.get("message") or code)
            record(f"blocker:{code}", "pending", message)
        for missing in container.get("missing") or ():
            name = str(missing)
            label = _text(language, f"缺少 {name}", f"Missing {name}")
            record(f"missing:{name}", "pending", label)

    error = _error_message(payload.get("error"))
    if error and not any(category == "failed" for category, _ in records.values()):
        category = "failed" if overall in _FAILED else "pending"
        record("root:error", category, error)

    result = {"completed": [], "reused": [], "failed": [], "pending": []}
    for category, label in records.values():
        result[category].append(label)
    return result


def _next_lines(payload: Mapping[str, Any], language: Language) -> list[str]:
    command = _first_mapping_value(payload, "recommended_command")
    action = _first_mapping_value(payload, "next_action")
    template_path = _first_mapping_value(payload, "template_path")
    template_guide = payload.get("template_guide")
    if template_path:
        lines = [_text(
            language, f"填写模板：{template_path}", f"Complete template: {template_path}",
        )]
        if isinstance(template_guide, Mapping):
            required = template_guide.get("required_fields")
            if isinstance(required, (list, tuple)) and required:
                lines.append(_text(
                    language, f"必填字段：{', '.join(map(str, required))}",
                    f"Required fields: {', '.join(map(str, required))}",
                ))
            instructions = template_guide.get("instructions")
            if isinstance(instructions, Mapping) and instructions.get(language):
                lines.append(str(instructions[language]))
        if action:
            lines.append(_localized(_ACTION_LABELS, str(action), language))
        return lines
    if command or action:
        lines = []
        if action:
            lines.append(_localized(_ACTION_LABELS, str(action), language))
        if command:
            lines.append(f"$ {command}")
        return lines

    summary = payload.get("summary")
    if isinstance(summary, Mapping):
        publish = summary.get("publish")
        delivery = summary.get("delivery")
        preprocess = summary.get("preprocess")
        if isinstance(publish, Mapping) and publish.get("status") == "succeeded":
            return [_text(language, "本集工作流已全部完成", "This episode workflow is complete")]
        if (isinstance(delivery, Mapping) and delivery.get("status") == "succeeded"
                and not payload.get("deferred_products")):
            return [
                _text(language, "开始外部文件交付", "Start external file delivery"),
                "$ bmlsub ws end",
            ]
        if isinstance(preprocess, Mapping) and preprocess.get("status") == "succeeded":
            return [_text(
                language,
                "交由人工翻译、校对，并使用 Aegisub 收集字体",
                "Continue with human translation, review, and Aegisub font collection",
            )]
    return []


def format_workstation_summary(payload: Mapping[str, Any], *, language: Language = "zh") -> str:
    """Format one workstation quick-mode payload without exposing its raw JSON."""
    if language not in {"zh", "en"}:
        raise ValueError("unsupported summary language")
    episode_id = _first_mapping_value(payload, "episode_id")
    title = _text(language, "Workstation 快速模式", "Workstation quick mode")
    if episode_id:
        title += _text(language, f" · 单集 {episode_id}", f" · Episode {episode_id}")
    lines = [title, _text(language, "结果：", "Result: ") + _status_label(payload.get("status"), language)]
    items = _collect_items(payload, language)
    headings = {
        "completed": _text(language, "已完成", "Completed"),
        "reused": _text(language, "已复用", "Reused"),
        "failed": _text(language, "失败", "Failed"),
        "pending": _text(language, "待处理", "Pending"),
    }
    markers = {"completed": "完成", "reused": "复用", "failed": "失败", "pending": "待处理"}
    if language == "en":
        markers = {"completed": "done", "reused": "reused", "failed": "failed", "pending": "pending"}
    for category in ("completed", "reused", "failed", "pending"):
        if not items[category]:
            continue
        lines.extend(("", headings[category]))
        lines.extend(f"  [{markers[category]}] {item}" for item in items[category])
    next_lines = _next_lines(payload, language)
    if next_lines:
        lines.extend(("", _text(language, "下一步", "Next")))
        lines.extend(f"  {item}" for item in next_lines)
    return "\n".join(lines)
