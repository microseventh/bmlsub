"""Public command routing for ws/build/rebuild."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Callable, TextIO

from .build import BuildContext, execute_plan, plan_operation
from .operations import OPERATION_NAMES, OPERATION_REGISTRY


InputFn = Callable[[str], str]


def operation_menu(*, rebuild: bool, directory: Path | str = ".") -> list[dict[str, object]]:
    context = BuildContext(directory)
    manifest = context.manifest()
    recorded = manifest.get("operations", {})
    rows = []
    for name in OPERATION_NAMES:
        spec = OPERATION_REGISTRY[name]
        if not spec.can_rebuild:
            availability = "not_rebuildable"
        elif recorded.get(name):
            availability = "rebuildable"
        else:
            availability = "no_previous_record"
        rows.append({**spec.to_dict(), "availability": availability if rebuild else "buildable"})
    return rows


def _prompt(prompt: str, *, input_fn: InputFn, output: TextIO) -> str:
    print(prompt, end="", file=output, flush=True)
    return input_fn("").strip()


def _default(prompt: str, default: str, *, input_fn: InputFn, output: TextIO) -> str:
    value = _prompt(f"{prompt} [{default}]: ", input_fn=input_fn, output=output)
    return value or default


def _yes_no(prompt: str, default: bool, *, input_fn: InputFn, output: TextIO) -> bool:
    marker = "Y/n" if default else "y/N"
    value = _prompt(f"{prompt} [{marker}]: ", input_fn=input_fn, output=output).casefold()
    if not value:
        return default
    return value in {"y", "yes"}


def _choose_operation(*, rebuild: bool, directory: Path | str, input_fn: InputFn,
                      output: TextIO) -> str:
    rows = operation_menu(rebuild=rebuild, directory=directory)
    print("Available operations:", file=output)
    for index, row in enumerate(rows, 1):
        marker = f" ({row['availability']})" if rebuild else ""
        print(f"  {index}. {row['name']:<8} {row['label']}{marker}", file=output)
    value = _prompt("Select a number or operation name: ", input_fn=input_fn, output=output)
    if value.isdigit() and 1 <= int(value) <= len(rows):
        return str(rows[int(value) - 1]["name"])
    if value in OPERATION_REGISTRY:
        return value
    raise ValueError(f"unknown operation selection: {value}")


def _questions(operation: str, *, rebuild: bool, cwd: Path, input_fn: InputFn,
               output: TextIO) -> tuple[bool, dict[str, object]]:
    recursive = False
    options: dict[str, object] = {}
    if OPERATION_REGISTRY[operation].input_kind not in {"config", "receipt"}:
        recursive = _yes_no("Scan subdirectories", False, input_fn=input_fn, output=output)
    if operation == "bgminfo":
        options["title_chs"] = _prompt("Simplified Chinese title: ", input_fn=input_fn, output=output)
        options["title_cht"] = _prompt("Traditional Chinese title (optional): ", input_fn=input_fn, output=output) or None
        options["romanized_title"] = _prompt("Romanized title: ", input_fn=input_fn, output=output)
        options["group_chs"] = _prompt("Simplified Chinese release group: ", input_fn=input_fn, output=output)
        options["group_cht"] = _prompt("Traditional Chinese release group (optional): ", input_fn=input_fn, output=output) or None
        options["bgm_id"] = _prompt("Bangumi ID (optional): ", input_fn=input_fn, output=output) or None
        options["anime_id"] = _prompt("Anime ID (optional): ", input_fn=input_fn, output=output) or None
    elif operation == "ensub":
        options["subtitle_language"] = _default("Subtitle language", "eng", input_fn=input_fn, output=output)
        options["include_unknown_language"] = _yes_no(
            "Include subtitle tracks without a language", False, input_fn=input_fn, output=output,
        )
    elif operation == "trans":
        options["audio_language"] = _default("Audio language", "jpn", input_fn=input_fn, output=output)
        options["strategy"] = _default("Transcription strategy (none/quick/full/custom)", "none",
                                       input_fn=input_fn, output=output)
        if options["strategy"] not in {"none", "quick", "full", "custom"}:
            raise ValueError("transcription strategy must be none, quick, full, or custom")
        options["transcription_language"] = _default(
            "Transcription language", "ja", input_fn=input_fn, output=output,
        )
        if options["strategy"] == "custom":
            options["transcription_mode"] = _default(
                "Whisper mode (direct/chunked)", "direct", input_fn=input_fn, output=output,
            )
            options["model"] = _prompt("MLX Whisper model: ", input_fn=input_fn, output=output)
            if options["transcription_mode"] == "chunked":
                options["chunk_seconds"] = float(_default(
                    "Chunk seconds", "240", input_fn=input_fn, output=output,
                ))
                options["overlap_seconds"] = float(_default(
                    "Overlap seconds", "5", input_fn=input_fn, output=output,
                ))
    elif operation == "pubinfo":
        options["ssh_alias"] = _prompt("OpenSSH host alias: ", input_fn=input_fn, output=output)
        options["remote_root"] = _prompt("VPS absolute target directory: ", input_fn=input_fn, output=output)
        options["qb_origin"] = _default("qBittorrent HTTPS origin", "https://127.0.0.1:8080",
                                        input_fn=input_fn, output=output)
        options["qb_save_path"] = _prompt("qBittorrent container save path: ", input_fn=input_fn, output=output)
        options["r2_profile"] = _prompt("Existing R2 credential profile: ", input_fn=input_fn, output=output)
        options["r2_bucket"] = _default("R2 bucket", "bml", input_fn=input_fn, output=output)
        options["anibt_profile"] = _prompt("Existing Anibt credential profile: ", input_fn=input_fn, output=output)
    elif operation == "encode":
        options["product"] = _default("Product (transcode/hardsub/mux/combo)", "transcode",
                                      input_fn=input_fn, output=output).casefold()
        if options["product"] not in {"transcode", "hardsub", "mux", "combo"}:
            raise ValueError("product must be transcode, hardsub, mux, or combo")
        if options["product"] != "mux":
            options["crf"] = int(_default("CRF", "20" if options["product"] == "hardsub" else "23",
                                          input_fn=input_fn, output=output))
            options["preset"] = _default("Encoder preset", "medium", input_fn=input_fn, output=output)
    elif operation == "torrent":
        tracker_text = _prompt("Tracker URLs (comma separated): ", input_fn=input_fn, output=output)
        options["trackers"] = [item.strip() for item in tracker_text.split(",") if item.strip()]
        if not options["trackers"]:
            raise ValueError("at least one tracker URL is required")
        options["torrent_format"] = _default("Torrent format (v1/hybrid)", "v1",
                                             input_fn=input_fn, output=output)
        options["private"] = _yes_no("Private Torrent", True, input_fn=input_fn, output=output)
        options["comment"] = _prompt("Comment (optional): ", input_fn=input_fn, output=output)
    elif operation == "upr2":
        options["r2_profile"] = _prompt("Existing R2 credential profile: ", input_fn=input_fn, output=output)
        options["bucket"] = _default("R2 bucket", "bml", input_fn=input_fn, output=output)
        options["object_prefix"] = _default("Object prefix", f"{cwd.name}/", input_fn=input_fn, output=output)
    elif operation == "dlvps":
        options["remote_pull_profile"] = _prompt("Existing remote-pull credential profile: ", input_fn=input_fn, output=output)
        options["remote_root"] = _prompt("VPS absolute target directory: ", input_fn=input_fn, output=output)
    elif operation == "seed":
        options["qb_profile"] = _prompt("Existing qBittorrent credential profile: ", input_fn=input_fn, output=output)
        options["ssh_profile"] = _prompt("Existing SSH profile: ", input_fn=input_fn, output=output)
        options["qb_origin"] = _default("qBittorrent HTTPS origin", "https://127.0.0.1:8080",
                                        input_fn=input_fn, output=output)
        options["qb_port"] = int(_default("Remote qBittorrent port", "8080", input_fn=input_fn, output=output))
        options["qb_save_path"] = _default("qBittorrent container save path", "/downloads",
                                           input_fn=input_fn, output=output)
    elif operation == "anibt":
        options["anibt_profile"] = _prompt("Existing Anibt credential profile: ", input_fn=input_fn, output=output)
        options["anime_id_type"] = _default("Anime ID type (bgm/anilist/mal/anidb)", "bgm",
                                            input_fn=input_fn, output=output)
        options["anime_id"] = _prompt("Anime ID: ", input_fn=input_fn, output=output)
        options["title"] = _prompt("Release title: ", input_fn=input_fn, output=output)
        options["episode_key"] = _prompt("Episode key: ", input_fn=input_fn, output=output)
        options["resolution"] = _default("Resolution", "1080p", input_fn=input_fn, output=output)
        language = _default("Languages (comma separated)", "CHS,JP", input_fn=input_fn, output=output)
        options["language"] = [item.strip().upper() for item in language.split(",") if item.strip()]
        options["subtitle"] = _default("Subtitle mode", "INTERNAL", input_fn=input_fn, output=output).upper()
        options["format"] = _default("Container", "MKV", input_fn=input_fn, output=output).upper()
        options["notes"] = _prompt("Release notes (optional): ", input_fn=input_fn, output=output)
        options["nyaa"] = _yes_no("Syndicate to Nyaa", False, input_fn=input_fn, output=output)
    return recursive, options


def _print_plan(plan: dict[str, object], output: TextIO) -> None:
    print(json.dumps({
        "operation": plan.get("operation"), "mode": plan.get("mode"),
        "discovery": plan.get("discovery"), "mappings": plan.get("mappings"),
        "summary": plan.get("summary"), "blockers": plan.get("blockers"),
    }, ensure_ascii=False, indent=2), file=output)


def run_operation(operation: str | None, *, rebuild: bool, directory: Path | str = ".",
                  input_fn: InputFn = input, input_stream: TextIO | None = None,
                  output: TextIO | None = None) -> dict[str, object]:
    stream = input_stream or sys.stdin
    target_output = output or sys.stderr
    cwd = Path(directory).expanduser().resolve()
    if operation is None and not stream.isatty():
        return {
            "status": "needs_review", "operation": None,
            "available_operations": operation_menu(rebuild=rebuild, directory=cwd),
            "next_action": "run_in_tty_and_select_one_operation",
        }
    if operation is None:
        operation = _choose_operation(rebuild=rebuild, directory=cwd,
                                      input_fn=input_fn, output=target_output)
    if rebuild and not OPERATION_REGISTRY[operation].can_rebuild:
        from .rebuild import rebuild_refusal
        refusal = rebuild_refusal(operation)
        assert refusal is not None
        return refusal
    if not stream.isatty():
        return {
            "status": "needs_review", "operation": operation,
            "error": {"code": "interactive_review_required",
                      "message": "build and rebuild require a TTY questionnaire"},
            "next_action": f"bmlsub {'rebuild' if rebuild else 'build'} {operation}",
        }
    recursive, options = _questions(operation, rebuild=rebuild, cwd=cwd,
                                    input_fn=input_fn, output=target_output)
    plan = plan_operation(operation, cwd, rebuild=rebuild, recursive=recursive, options=options)
    _print_plan(plan, target_output)
    if plan["status"] != "planned":
        return plan
    expected = str(plan["confirmation_word"])
    entered = _prompt(f"Type {expected!r} to execute: ", input_fn=input_fn, output=target_output)
    if entered != expected:
        return {"status": "awaiting_confirmation", "operation": operation,
                "plan": plan, "next_action": f"bmlsub {'rebuild' if rebuild else 'build'} {operation}"}
    return execute_plan(plan, context=BuildContext(cwd))
