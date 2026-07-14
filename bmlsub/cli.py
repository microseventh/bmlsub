"""bmlsub 命令行入口。"""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import redirect_stdout
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Callable

from .config import (
    PipelineConfig,
    ProjectConfig,
    ProjectNaming,
    WorkstationConfig,
    load_project_config,
    parse_episode_ids,
    project_config_path,
    save_project_config,
)
from .pipeline import Pipeline
from .hanvert import extract_ass_analysis


Handler = Callable[[argparse.Namespace, Pipeline], Any]


DEFAULT_ARGUMENTS = {
    "work_dir": ".",
    "output_transcripts_dir": "./output_transcripts",
    "language": "ja",
    "chunk_sec": 240,
    "overlap_sec": 5,
    "group": "Billion Meta Lab",
    "name_chs": "作品名",
    "name_cht": "作品名",
    "romaji": "Romaji",
    "raw_dir_name": "RAW",
    "sub_dir_name": "CHS&JPN",
    "sub_tj_dir_name": "CHT&JPN",
    "hevc_subdir_name": "HEVC-10Bit",
    "r2_prefix": "",
    "notes": "",
    "qb_user": "admin",
    "qb_pass": "",
    "download_base": "/downloads",
}


def serialize_result(value: Any) -> Any:
    """把流水线返回值转换为可供 JSON 消费的数据。"""

    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        summary = getattr(value, "summary", None)
        return serialize_result(summary() if callable(summary) else asdict(value))
    summary = getattr(value, "summary", None)
    if callable(summary):
        return serialize_result(summary())
    if isinstance(value, dict):
        return {str(key): serialize_result(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [serialize_result(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "output_path"):
        return str(value.output_path)
    return str(value)


def build_project_naming_from_args(args: argparse.Namespace) -> ProjectNaming:
    return ProjectNaming(
        group=args.group,
        name_chs=args.name_chs,
        name_cht=args.name_cht,
        romaji=args.romaji,
    )


def build_pipeline_config_from_args(args: argparse.Namespace) -> PipelineConfig:
    return PipelineConfig(
        work_dir=Path(args.work_dir),
        output_transcripts_dir=Path(args.output_transcripts_dir),
        whisper_fast_model=args.direct_model or PipelineConfig.whisper_fast_model,
        whisper_detailed_model=args.chunked_model or PipelineConfig.whisper_detailed_model,
        language=args.language,
        chunk_sec=args.chunk_sec,
        overlap_sec=args.overlap_sec,
        project=build_project_naming_from_args(args),
    )


def build_workstation_from_args(args: argparse.Namespace) -> WorkstationConfig:
    return WorkstationConfig(
        root_dir=Path(args.root_dir),
        episode_ids=parse_episode_ids(args.episodes),
        group=args.group,
        name_chs=args.name_chs,
        name_cht=args.name_cht,
        romaji=args.romaji,
        raw_dir_name=args.raw_dir_name,
        sub_dir_name=args.sub_dir_name,
        sub_tj_dir_name=args.sub_tj_dir_name,
        hevc_subdir_name=args.hevc_subdir_name,
        r2_prefix=args.r2_prefix,
        bgm_id=args.bgm_id,
        notes=args.notes,
    )


def _episode_dir(args: argparse.Namespace) -> Path:
    return Path(args.episode_dir or args.work_dir)


def _episode_kwargs(args: argparse.Namespace, *, include_project: bool = True) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "episode_id": args.episode_id,
        "source_video": args.source_video,
        "chs_subtitle": args.chs_subtitle,
        "cht_subtitle": args.cht_subtitle,
    }
    if include_project:
        kwargs.update({
            "prefix_chs": getattr(args, "prefix_chs", None),
            "prefix_cht": getattr(args, "prefix_cht", None),
            "project": build_project_naming_from_args(args),
        })
    return kwargs


def _inspect_episode(args: argparse.Namespace, pipe: Pipeline) -> Any:
    return pipe.inspect_episode(_episode_dir(args), **_episode_kwargs(args))


def _plan_episode(args: argparse.Namespace, pipe: Pipeline) -> Any:
    return pipe.plan_episode(_episode_dir(args), **_episode_kwargs(args))


def _extract_audio(args: argparse.Namespace, pipe: Pipeline) -> Any:
    return pipe.extract_audio(_episode_dir(args), **_episode_kwargs(args, include_project=False))


def _extract_subs(args: argparse.Namespace, pipe: Pipeline) -> Any:
    return pipe.extract_subtitles(
        _episode_dir(args), smart=args.smart, **_episode_kwargs(args, include_project=False)
    )


def _extract_media(args: argparse.Namespace, pipe: Pipeline) -> Any:
    return pipe.extract_media(
        _episode_dir(args),
        episodes=parse_episode_ids(args.episodes) or None,
        smart_subs=not args.all_subs,
    )


def _transcribe(args: argparse.Namespace, pipe: Pipeline) -> Any:
    return pipe.transcribe_episode(
        _episode_dir(args),
        direct_model=args.direct_model,
        chunked_model=args.chunked_model,
        manual_cuts=args.manual_cut or None,
        **_episode_kwargs(args, include_project=False),
    )


def _encode(args: argparse.Namespace, pipe: Pipeline) -> Any:
    return pipe.encode_episode(_episode_dir(args), **_episode_kwargs(args, include_project=False))


def _validate_subs(args: argparse.Namespace, pipe: Pipeline) -> Any:
    return pipe.validate_subtitles(
        _episode_dir(args),
        ensure_cht=args.ensure_cht,
        converter=args.converter,
        api_url=args.conversion_api_url,
        timeout=args.conversion_timeout,
        regenerate_cht=args.regenerate_cht,
        full_file=args.full_file_hanvert,
        fallback_to_full_file=not args.no_full_file_fallback,
        **_episode_kwargs(args, include_project=False),
    )


def _analyze_ass(args: argparse.Namespace, pipe: Pipeline | None = None) -> Any:
    ass_path = Path(args.ass_file).expanduser().resolve()
    output = (
        Path(args.output).expanduser().resolve()
        if args.output
        else ass_path.with_name(f"{ass_path.stem}.analysis.json")
    )
    result = extract_ass_analysis(
        ass_path,
        output_path=output,
        include_comments=args.include_comments,
    )
    return {"output": str(output), "analysis": result}


def _package(args: argparse.Namespace, pipe: Pipeline) -> Any:
    return pipe.package_episode(
        _episode_dir(args),
        mkv_template=args.mkv_template,
        chs_template=args.chs_template,
        cht_template=args.cht_template,
        **_episode_kwargs(args),
    )


def _upload_r2(args: argparse.Namespace, pipe: Pipeline) -> Any:
    r2_kwargs = {
        key: value for key, value in {
            "account_id": args.r2_account_id,
            "access_key_id": args.r2_access_key_id,
            "secret_access_key": args.r2_secret_access_key,
            "bucket_name": args.r2_bucket_name,
            "endpoint": args.r2_endpoint,
        }.items() if value is not None
    }
    return pipe.upload_files_to_r2(args.files, remote_folder=args.r2_prefix, **r2_kwargs)


def _seed(args: argparse.Namespace, pipe: Pipeline) -> Any:
    return pipe.seed_torrents(
        [Path(path) for path in args.files],
        qb_host=args.qb_host,
        qb_user=args.qb_user,
        qb_pass=args.qb_pass,
        download_base=args.download_base,
    )


def _process(args: argparse.Namespace, pipe: Pipeline) -> Any:
    skip_upload = args.skip_upload or args.local_only
    skip_seed = args.skip_seed or args.local_only
    manual_cuts = {args.episode_id: args.manual_cut} if args.episode_id and args.manual_cut else None
    return pipe.process_episode(
        _episode_dir(args),
        manual_cuts=manual_cuts,
        direct_model=args.direct_model,
        chunked_model=args.chunked_model,
        mkv_template=args.mkv_template,
        chs_template=args.chs_template,
        cht_template=args.cht_template,
        r2_prefix=args.r2_prefix,
        qb_host=args.qb_host,
        skip_transcribe=args.skip_transcribe,
        skip_encode=args.skip_encode,
        skip_package=args.skip_package,
        skip_upload=skip_upload,
        skip_seed=skip_seed,
        **_episode_kwargs(args),
    )


def _workstation_action(args: argparse.Namespace, pipe: Pipeline) -> Any:
    workstation = build_workstation_from_args(args)
    actions: dict[str, Callable[[WorkstationConfig], Any]] = {
        "inspect": pipe.inspect_workstation,
        "plan": pipe.plan_workstation,
        "validate-subs": pipe.validate_workstation_subtitles,
        "encode-hevc": pipe.encode_workstation_hevc,
        "build-release-batch": pipe.build_release_batch,
    }
    return actions[args.workstation_action](workstation)


def _add_global_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--work-dir", help="流水线默认工作目录（默认：当前目录）")
    parser.add_argument("--output-transcripts-dir", help="转录输出根目录")
    parser.add_argument("--language", help="转录语言代码（默认：ja）")
    parser.add_argument("--direct-model", help="快速转录模型")
    parser.add_argument("--chunked-model", help="分段转录模型")
    parser.add_argument("--chunk-sec", type=int, help="转录分段秒数")
    parser.add_argument("--overlap-sec", type=int, help="转录分段重叠秒数")
    parser.add_argument("--group", help="字幕组名称")
    parser.add_argument("--name-chs", help="简体作品名")
    parser.add_argument("--name-cht", help="繁体作品名")
    parser.add_argument("--romaji", help="作品罗马字")


def _add_episode_options(parser: argparse.ArgumentParser, *, require_id: bool = False) -> None:
    parser.add_argument("--episode-dir", help="单集目录（默认使用 --work-dir）")
    parser.add_argument("--episode-id", help="集号，如 01")
    parser.add_argument("--source-video", type=Path, help="显式指定源视频")
    parser.add_argument("--chs-subtitle", type=Path, help="显式指定简体字幕")
    parser.add_argument("--cht-subtitle", type=Path, help="显式指定繁体字幕")


def _add_naming_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--prefix-chs", help="覆盖简体产物前缀")
    parser.add_argument("--prefix-cht", help="覆盖繁体产物前缀")


def _add_package_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--mkv-template", help="旧式 MKV 输出模板")
    parser.add_argument("--chs-template", help="旧式简体 MP4 输出模板")
    parser.add_argument("--cht-template", help="旧式繁体 MP4 输出模板")


def _add_workstation_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root-dir", help="合集项目根目录（默认：当前目录）")
    parser.add_argument("--episodes", help="集号列表或范围，如 01-12、01,03,05；留空则从配置或 RAW 推断")
    parser.add_argument("--raw-dir-name")
    parser.add_argument("--sub-dir-name")
    parser.add_argument("--sub-tj-dir-name")
    parser.add_argument("--hevc-subdir-name")
    parser.add_argument("--r2-prefix")
    parser.add_argument("--bgm-id", type=int)
    parser.add_argument("--notes")


def _command(subparsers: argparse._SubParsersAction, name: str, help_text: str,
             handler: Handler, *, episode: bool = False, require_id: bool = False,
             naming: bool = False, hidden: bool = False) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        name,
        help=argparse.SUPPRESS if hidden else help_text,
        description=help_text,
    )
    _add_global_options(parser)
    if episode:
        _add_episode_options(parser, require_id=require_id)
    if naming:
        _add_naming_options(parser)
    parser.set_defaults(handler=handler)
    return parser


def _configure_extract_subs(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--smart", action="store_true", help="按语言优先级智能筛选字幕")


def _configure_extract_media(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--episodes", help="集号列表或范围")
    parser.add_argument("--all-subs", action="store_true", help="提取全部字幕而非智能筛选")


def _configure_transcribe(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--manual-cut", action="append", help="手动切点，可重复，如 01:30")


def _configure_validate(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--ensure-cht", action="store_true", help="缺少繁体时由简体生成繁体")
    parser.add_argument("--converter", help="繁化姬模式，如 Taiwan、Traditional、Hongkong")
    parser.add_argument("--conversion-api-url", help="字幕转换 API URL")
    parser.add_argument("--conversion-timeout", type=int, help="字幕转换超时秒数")
    parser.add_argument(
        "--full-file-hanvert",
        action="store_true",
        help="跳过 ASS 分析，将完整文件直接提交给繁化姬",
    )
    parser.add_argument(
        "--no-full-file-fallback",
        action="store_true",
        help="ASS 感知无法可靠判断时抛错，不自动改用全文件繁化",
    )
    regenerate = parser.add_mutually_exclusive_group()
    regenerate.add_argument("--regenerate-cht", dest="regenerate_cht", action="store_true",
                            help="以简体重建已有繁体字幕")
    regenerate.add_argument("--keep-existing-cht", dest="regenerate_cht", action="store_false",
                            help="保留已有繁体字幕")
    parser.set_defaults(regenerate_cht=None)


def _configure_analyze_ass(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--ass-file", required=True, type=Path, help="要分析的 ASS 文件")
    parser.add_argument("--output", type=Path, help="JSON 输出路径（默认：<原名>.analysis.json）")
    parser.add_argument("--include-comments", action="store_true", help="将 Comment 事件纳入语言分组")


def _configure_process(parser: argparse.ArgumentParser) -> None:
    _add_package_options(parser)
    parser.add_argument("--manual-cut", action="append", help="手动转录切点，可重复")
    parser.add_argument("--r2-prefix")
    parser.add_argument("--qb-host")
    parser.add_argument("--local-only", action="store_true", help="跳过上传和做种")
    parser.add_argument("--skip-transcribe", action="store_true")
    parser.add_argument("--skip-encode", action="store_true")
    parser.add_argument("--skip-package", action="store_true")
    parser.add_argument("--skip-upload", action="store_true")
    parser.add_argument("--skip-seed", action="store_true")


def _configure_upload(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("files", nargs="+", type=Path, help="要上传的本地文件")
    parser.add_argument("--r2-prefix", help="远端目录前缀")
    parser.add_argument("--r2-account-id")
    parser.add_argument("--r2-access-key-id")
    parser.add_argument("--r2-secret-access-key")
    parser.add_argument("--r2-bucket-name")
    parser.add_argument("--r2-endpoint")


def _configure_seed(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("files", nargs="+", type=Path, help="已生成对应 torrent 的成品文件")
    parser.add_argument("--qb-host", help="qBittorrent 地址")
    parser.add_argument("--qb-user")
    parser.add_argument("--qb-pass")
    parser.add_argument("--download-base")


def _config_from_args(args: argparse.Namespace, existing: ProjectConfig | None = None) -> ProjectConfig:
    base = existing or ProjectConfig()
    project = ProjectNaming(
        group=args.group if args.group is not None else base.project.group,
        name_chs=args.name_chs if args.name_chs is not None else base.project.name_chs,
        name_cht=args.name_cht if args.name_cht is not None else base.project.name_cht,
        romaji=args.romaji if args.romaji is not None else base.project.romaji,
    )
    episodes = parse_episode_ids(args.episodes) if args.episodes is not None else base.episode_ids
    return ProjectConfig(
        project=project,
        episode_ids=episodes,
        r2_prefix=args.r2_prefix if args.r2_prefix is not None else base.r2_prefix,
        bgm_id=args.bgm_id if args.bgm_id is not None else base.bgm_id,
        notes=args.notes if args.notes is not None else base.notes,
        qb_host=args.qb_host if args.qb_host is not None else base.qb_host,
    )


def _config_init(args: argparse.Namespace, pipe: Pipeline | None = None) -> Any:
    config = _config_from_args(args)
    path = save_project_config(config, overwrite=args.force)
    return {"path": str(path), "config": config.to_dict()}


def _config_show(args: argparse.Namespace, pipe: Pipeline | None = None) -> Any:
    config = load_project_config()
    if config is None:
        raise FileNotFoundError(f"项目配置不存在: {project_config_path()}")
    return config.to_dict()


def _config_update(args: argparse.Namespace, pipe: Pipeline | None = None) -> Any:
    existing = load_project_config()
    if existing is None:
        raise FileNotFoundError(f"项目配置不存在: {project_config_path()}；请先运行 bmlsub config init")
    config = _config_from_args(args, existing)
    path = save_project_config(config, overwrite=True)
    return {"path": str(path), "config": config.to_dict()}


def _add_config_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--group", help="字幕组名称")
    parser.add_argument("--name-chs", help="简体作品名")
    parser.add_argument("--name-cht", help="繁体作品名")
    parser.add_argument("--romaji", help="作品罗马字")
    parser.add_argument("--episodes", help="集号列表或范围，如 01-12、01,03,05")
    parser.add_argument("--r2-prefix", help="R2 远端目录前缀")
    parser.add_argument("--bgm-id", type=int, help="发布条目 BGM ID")
    parser.add_argument("--notes", help="发布备注")
    parser.add_argument("--qb-host", help="qBittorrent 地址（不保存密码）")


def _register_config_actions(subparsers: argparse._SubParsersAction) -> None:
    init = subparsers.add_parser("init", help="创建当前目录项目配置")
    _add_config_options(init)
    init.add_argument("--force", action="store_true", help="覆盖已有配置")
    init.set_defaults(handler=_config_init)

    show = subparsers.add_parser("show", help="显示当前目录项目配置")
    show.set_defaults(handler=_config_show)

    update = subparsers.add_parser("update", help="更新当前目录项目配置")
    _add_config_options(update)
    update.set_defaults(handler=_config_update)


def _register_episode_actions(subparsers: argparse._SubParsersAction) -> None:
    _command(subparsers, "inspect", "检查资源", _inspect_episode, episode=True, naming=True)
    _command(subparsers, "plan", "规划流水线", _plan_episode, episode=True, naming=True)
    _command(subparsers, "audio", "提取音轨", _extract_audio, episode=True, require_id=True)

    subs = _command(subparsers, "subs", "提取字幕轨", _extract_subs,
                    episode=True, require_id=True)
    _configure_extract_subs(subs)

    media = _command(subparsers, "media", "批量提取目录内媒体轨", _extract_media, episode=True)
    _configure_extract_media(media)

    transcribe = _command(subparsers, "transcribe", "转录已提取音轨", _transcribe,
                          episode=True, require_id=True)
    _configure_transcribe(transcribe)
    _command(subparsers, "encode", "编码 HEVC 视频", _encode, episode=True, require_id=True)

    validate = _command(subparsers, "validate", "校验并标准化字幕", _validate_subs,
                        episode=True, require_id=True)
    _configure_validate(validate)

    analyze = _command(subparsers, "analyze-ass", "提取 ASS 中日文文本与统计", _analyze_ass)
    _configure_analyze_ass(analyze)

    package = _command(subparsers, "package", "封装产物", _package,
                       episode=True, require_id=True, naming=True)
    _add_package_options(package)

    run = _command(subparsers, "run", "运行完整流程", _process, episode=True, naming=True)
    _configure_process(run)


def _register_workstation_actions(subparsers: argparse._SubParsersAction) -> None:
    actions = {
        "inspect": ("检查目录与素材", "inspect"),
        "plan": ("规划合集流水线", "plan"),
        "validate": ("批量校验字幕", "validate-subs"),
        "encode": ("生成 HEVC 编码计划", "encode-hevc"),
        "release": ("规划合集发布种子", "build-release-batch"),
    }
    for name, (help_text, action) in actions.items():
        command = _command(subparsers, name, help_text, _workstation_action)
        _add_workstation_options(command)
        command.set_defaults(workstation_action=action)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bmlsub",
        description="BML 动漫字幕制作、编码、封装与发布流水线",
    )
    parser.add_argument("--version", action="version", version="%(prog)s 0.2.0")
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        metavar="{episode,workstation,upload,seed,config}",
    )

    episode = subparsers.add_parser("episode", help="单集与媒体流程")
    episode_actions = episode.add_subparsers(dest="episode_action", required=True, metavar="ACTION")
    _register_episode_actions(episode_actions)

    workstation = subparsers.add_parser("workstation", help="合集流程")
    workstation_actions = workstation.add_subparsers(
        dest="workstation_command", required=True, metavar="ACTION"
    )
    _register_workstation_actions(workstation_actions)

    upload = _command(subparsers, "upload", "上传文件到 Cloudflare R2", _upload_r2)
    _configure_upload(upload)

    seed = _command(subparsers, "seed", "通过 qBittorrent 做种", _seed)
    _configure_seed(seed)

    config = subparsers.add_parser("config", help="管理当前目录项目配置")
    config_actions = config.add_subparsers(dest="config_action", required=True, metavar="ACTION")
    _register_config_actions(config_actions)
    return parser


def _normalize_argv(argv: list[str]) -> list[str]:
    normalized = list(argv)
    legacy_commands = {
        "inspect-episode": ["episode", "inspect"],
        "plan-episode": ["episode", "plan"],
        "extract-audio": ["episode", "audio"],
        "extract-subs": ["episode", "subs"],
        "extract-media": ["episode", "media"],
        "transcribe": ["episode", "transcribe"],
        "encode": ["episode", "encode"],
        "validate-subs": ["episode", "validate"],
        "analyze-ass": ["episode", "analyze-ass"],
        "package": ["episode", "package"],
        "process": ["episode", "run"],
        "upload-r2": ["upload"],
        "inspect-workstation": ["workstation", "inspect"],
        "plan-workstation": ["workstation", "plan"],
        "validate-workstation-subs": ["workstation", "validate"],
        "encode-workstation-hevc": ["workstation", "encode"],
        "build-release-batch": ["workstation", "release"],
    }
    if normalized and normalized[0] in legacy_commands:
        normalized = legacy_commands[normalized[0]] + normalized[1:]

    if normalized and normalized[0] == "episode":
        actions = {"inspect", "plan", "audio", "subs", "media", "transcribe",
                   "encode", "validate", "analyze-ass", "package", "run"}
        if len(normalized) == 1:
            normalized.insert(1, "run")
        elif normalized[1] not in actions and normalized[1] not in {"-h", "--help"}:
            normalized.insert(1, "run")

    workstation_aliases = {
        "validate-subs": "validate",
        "encode-hevc": "encode",
        "build-release-batch": "release",
    }
    if len(normalized) > 1 and normalized[0] == "workstation":
        normalized[1] = workstation_aliases.get(normalized[1], normalized[1])
    return normalized


def _apply_project_config(args: argparse.Namespace) -> None:
    if getattr(args, "command", None) == "config":
        return
    config = load_project_config()
    config_root = project_config_path().parent if config is not None else Path.cwd()
    if config is not None:
        project_values = {
            "group": config.project.group,
            "name_chs": config.project.name_chs,
            "name_cht": config.project.name_cht,
            "romaji": config.project.romaji,
        }
        for name, value in project_values.items():
            if hasattr(args, name) and getattr(args, name) is None:
                setattr(args, name, value)

        if hasattr(args, "episodes") and getattr(args, "episodes") is None and config.episode_ids:
            args.episodes = list(config.episode_ids)
        if hasattr(args, "episode_id") and getattr(args, "episode_id") is None:
            if len(config.episode_ids) == 1:
                args.episode_id = config.episode_ids[0]
        for name, value in {
            "r2_prefix": config.r2_prefix,
            "bgm_id": config.bgm_id,
            "notes": config.notes,
            "qb_host": config.qb_host,
        }.items():
            if hasattr(args, name) and getattr(args, name) is None and value not in (None, ""):
                setattr(args, name, value)

    for name, value in DEFAULT_ARGUMENTS.items():
        if hasattr(args, name) and getattr(args, name) is None:
            setattr(args, name, value)
    if hasattr(args, "root_dir") and args.root_dir is None:
        args.root_dir = str(config_root)
    if hasattr(args, "episodes") and isinstance(args.episodes, list):
        args.episodes = list(args.episodes)


def _validate_required_args(args: argparse.Namespace) -> None:
    episode_actions = {"audio", "subs", "transcribe", "encode", "validate", "package"}
    if getattr(args, "episode_action", None) in episode_actions and not args.episode_id:
        raise ValueError("缺少 episode_id：请传入 --episode-id，或在 bmlsub-project.json 中仅配置一集")
    if getattr(args, "command", None) == "seed" and not args.qb_host:
        raise ValueError("缺少 qb_host：请传入 --qb-host，或在 bmlsub-project.json 中配置")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(_normalize_argv(raw_argv))
    try:
        _apply_project_config(args)
        _validate_required_args(args)
        if getattr(args, "command", None) == "config":
            result = args.handler(args, None)
        else:
            pipe = Pipeline(build_pipeline_config_from_args(args))
            with redirect_stdout(sys.stderr):
                result = args.handler(args, pipe)
        print(json.dumps(serialize_result(result), ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
