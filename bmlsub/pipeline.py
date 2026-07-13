"""
流水线编排

设计原则：
- 每一步都可独立执行
- 每一步都显式检查自己的前置条件
- 保留 notebook 友好输出，同时返回结构化结果
- 单集模式与合集模式分层，但共享底层模块
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import PipelineConfig, ProjectNaming, WorkstationConfig
from .encode import Encoder
from .episode import EpisodeFiles
from .media import MediaExtractor
from .package import Packager, PackagingError
from .progress import PipelineTimer
from .publish import Publisher
from .r2upload import R2Uploader
from .subtitle import SubtitleValidator
from .torrent import TorrentCreator
from .transcribe import Transcriber, TranscriptionError


@dataclass
class StageStatus:
    name: str
    ready: bool
    missing: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "name": self.name,
            "ready": self.ready,
            "missing": list(self.missing),
            "outputs": list(self.outputs),
            "notes": list(self.notes),
        }


@dataclass
class EpisodeStagePlan:
    episode_id: str
    inspect: StageStatus
    extract_subtitles: StageStatus
    extract_audio: StageStatus
    transcribe: StageStatus
    encode_hevc: StageStatus
    validate_subtitles: StageStatus
    package: StageStatus

    def summary(self) -> dict:
        return {
            "episode_id": self.episode_id,
            "inspect": self.inspect.summary(),
            "extract_subtitles": self.extract_subtitles.summary(),
            "extract_audio": self.extract_audio.summary(),
            "transcribe": self.transcribe.summary(),
            "encode_hevc": self.encode_hevc.summary(),
            "validate_subtitles": self.validate_subtitles.summary(),
            "package": self.package.summary(),
        }


@dataclass
class WorkstationStage0Summary:
    workstation: dict
    stage0: StageStatus

    def summary(self) -> dict:
        payload = dict(self.workstation)
        payload["stage0"] = self.stage0.summary()
        return payload


@dataclass
class WorkstationBatchResult:
    mode: str
    stage: str
    ok: bool
    items: list[dict] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "mode": self.mode,
            "stage": self.stage,
            "ok": self.ok,
            "items": list(self.items),
            "missing": list(self.missing),
            "outputs": list(self.outputs),
            "notes": list(self.notes),
        }


class Pipeline:
    """完整流水线编排器。"""

    def __init__(self, config: PipelineConfig | None = None, **kwargs):
        self.config = config or PipelineConfig(**kwargs)
        self.work_dir = self.config.work_dir
        self._extractor: MediaExtractor | None = None
        self._transcriber: Transcriber | None = None
        self._encoder: Encoder | None = None
        self._validator: SubtitleValidator | None = None

    @property
    def extractor(self) -> MediaExtractor:
        if self._extractor is None:
            self._extractor = MediaExtractor(self.work_dir)
        return self._extractor

    @property
    def transcriber(self) -> Transcriber:
        if self._transcriber is None:
            self._transcriber = Transcriber(
                model=self.config.whisper_fast_model,
                language=self.config.language,
                chunk_sec=self.config.chunk_sec,
                overlap_sec=self.config.overlap_sec,
                output_root=self.config.output_transcripts_dir,
            )
        return self._transcriber

    @property
    def encoder(self) -> Encoder:
        if self._encoder is None:
            self._encoder = Encoder(self.config.hevc_preset, self.config.x264_preset)
        return self._encoder

    @property
    def validator(self) -> SubtitleValidator:
        if self._validator is None:
            self._validator = SubtitleValidator(self.config.sub_standard, config=self.config)
        return self._validator

    def context(self,
                episode_dir: Path | str,
                episode_id: str | None = None,
                prefix_chs: str | None = None,
                prefix_cht: str | None = None,
                project: ProjectNaming | None = None,
                source_video: Path | str | None = None,
                chs_subtitle: Path | str | None = None,
                cht_subtitle: Path | str | None = None) -> EpisodeFiles:
        return EpisodeFiles.discover(
            episode_dir,
            episode_id,
            prefix_chs=prefix_chs,
            prefix_cht=prefix_cht,
            config=self.config,
            project=project,
            source_video=source_video,
            chs_subtitle=chs_subtitle,
            cht_subtitle=cht_subtitle,
        )

    def inspect_episode(self, episode_dir: Path | str,
                        episode_id: str | None = None,
                        prefix_chs: str | None = None,
                        prefix_cht: str | None = None,
                        project: ProjectNaming | None = None,
                        source_video: Path | str | None = None,
                        chs_subtitle: Path | str | None = None,
                        cht_subtitle: Path | str | None = None) -> dict:
        return self.context(
            episode_dir,
            episode_id,
            prefix_chs=prefix_chs,
            prefix_cht=prefix_cht,
            project=project,
            source_video=source_video,
            chs_subtitle=chs_subtitle,
            cht_subtitle=cht_subtitle,
        ).summary()

    def plan_episode(self,
                     episode_dir: Path | str,
                     episode_id: str | None = None,
                     prefix_chs: str | None = None,
                     prefix_cht: str | None = None,
                     project: ProjectNaming | None = None,
                     source_video: Path | str | None = None,
                     chs_subtitle: Path | str | None = None,
                     cht_subtitle: Path | str | None = None) -> EpisodeStagePlan:
        ctx = self.context(
            episode_dir,
            episode_id,
            prefix_chs=prefix_chs,
            prefix_cht=prefix_cht,
            project=project,
            source_video=source_video,
            chs_subtitle=chs_subtitle,
            cht_subtitle=cht_subtitle,
        )
        packager = Packager(
            ctx.episode_dir,
            ctx.episode_id,
            self.config,
            source_video=source_video,
            chs_subtitle=chs_subtitle,
            cht_subtitle=cht_subtitle,
        )
        package_plan = packager.build_plan(
            prefix_chs=prefix_chs,
            prefix_cht=prefix_cht,
            project=project,
        )

        inspect = StageStatus(
            name="inspect",
            ready=True,
            outputs=[ctx.episode_id],
            notes=["统一资源发现已完成"],
        )
        extract_subtitles = StageStatus(
            name="extract_subtitles",
            ready=ctx.pure_mkv is not None,
            missing=[] if ctx.pure_mkv else [str(ctx.source_video_path or (ctx.episode_dir / f'{ctx.episode_id}.mkv'))],
            outputs=[str(path) for path in ctx.extracted_subtitles],
        )
        extract_audio = StageStatus(
            name="extract_audio",
            ready=ctx.pure_mkv is not None,
            missing=[] if ctx.pure_mkv else [str(ctx.source_video_path or (ctx.episode_dir / f'{ctx.episode_id}.mkv'))],
            outputs=[str(path) for path in ctx.extracted_audio],
        )
        transcribe = StageStatus(
            name="transcribe",
            ready=bool(ctx.extracted_audio),
            missing=[] if ctx.extracted_audio else [f"{ctx.episode_id}_audio_*.aac"],
            outputs=[str(path) for path in ctx.extracted_audio],
            notes=["依赖先提取音轨"] if not ctx.extracted_audio else [],
        )
        encode_hevc = StageStatus(
            name="encode_hevc",
            ready=ctx.pure_mkv is not None,
            missing=[] if ctx.pure_mkv else [str(ctx.source_video_path or (ctx.episode_dir / f'{ctx.episode_id}.mkv'))],
            outputs=[str(ctx.hevc_mkv)] if ctx.hevc_mkv else [],
        )
        validate_subtitles = StageStatus(
            name="validate_subtitles",
            ready=bool(ctx.all_subs),
            missing=[] if ctx.all_subs else [f"{ctx.episode_id}.chs&jpn.ass / {ctx.episode_id}.cht&jpn.ass"],
            outputs=[str(path) for path in ctx.all_subs],
        )
        package = StageStatus(
            name="package",
            ready=package_plan.has_mp4_inputs or package_plan.has_mkv_inputs,
            missing=sorted(set(package_plan.missing_for_mp4 + package_plan.missing_for_mkv)),
            outputs=[
                str(path) for path in (
                    package_plan.mp4_chs_output,
                    package_plan.mp4_cht_output,
                    package_plan.mkv_output,
                ) if path is not None
            ],
            notes=[
                "MP4 硬压与 MKV 内封是独立子步骤",
                "整体顺序建议参考 workstation.ipynb：先提取/校验，再编码/封装",
            ],
        )
        return EpisodeStagePlan(
            episode_id=ctx.episode_id,
            inspect=inspect,
            extract_subtitles=extract_subtitles,
            extract_audio=extract_audio,
            transcribe=transcribe,
            encode_hevc=encode_hevc,
            validate_subtitles=validate_subtitles,
            package=package,
        )

    def build_workstation(self,
                          root_dir: Path | str,
                          episode_ids: list[str] | str | None = None,
                          **kwargs) -> WorkstationConfig:
        return WorkstationConfig(root_dir=Path(root_dir), episode_ids=episode_ids or [], **kwargs)

    def inspect_workstation(self, workstation: WorkstationConfig | Path | str, **kwargs) -> WorkstationStage0Summary:
        ws = self._normalize_workstation(workstation, **kwargs)
        checks = ws.stage0_checks()
        missing_parts: list[str] = []
        summary = ws.missing_summary()
        if summary["source"]:
            missing_parts.append(f"缺少源视频: {', '.join(summary['source'])}")
        if summary["chs_sub"]:
            missing_parts.append(f"缺少简日字幕: {', '.join(summary['chs_sub'])}")
        if summary["cht_sub"]:
            missing_parts.append(f"缺少繁日字幕: {', '.join(summary['cht_sub'])}")

        stage0 = StageStatus(
            name="stage0",
            ready=not missing_parts,
            missing=missing_parts,
            outputs=[str(ws.raw_dir), str(ws.sub_dir), str(ws.sub_tj_dir)],
            notes=[
                f"共检查 {len(checks)} 集",
                "合集模式阶段 0 用于统一项目参数、目录布局与前置条件检查",
            ],
        )
        return WorkstationStage0Summary(workstation=ws.summary(), stage0=stage0)

    def plan_workstation(self, workstation: WorkstationConfig | Path | str, **kwargs) -> WorkstationBatchResult:
        ws = self._normalize_workstation(workstation, **kwargs)
        items: list[dict] = []
        outputs: list[str] = []
        missing: list[str] = []

        for ep_id in ws.effective_episode_ids:
            episode_dir = self._single_episode_dir(ws, ep_id)
            plan = self.plan_episode(
                episode_dir,
                episode_id=ep_id,
                prefix_chs=ws.prefix_chs,
                prefix_cht=ws.prefix_cht,
            )
            plan_summary = plan.summary()
            items.append(plan_summary)
            outputs.extend(stage["name"] for key, stage in plan_summary.items() if isinstance(stage, dict) and stage["ready"])
            for key, stage in plan_summary.items():
                if isinstance(stage, dict) and stage["missing"]:
                    for value in stage["missing"]:
                        marker = f"EP{ep_id}:{key}:{value}"
                        if marker not in missing:
                            missing.append(marker)

        return WorkstationBatchResult(
            mode="collection",
            stage="plan",
            ok=not missing,
            items=items,
            missing=missing,
            outputs=outputs,
            notes=["合集模式计划按集汇总单集阶段 readiness"],
        )

    def extract_subtitles(self,
                          episode_dir: Path | str,
                          episode_id: str,
                          smart: bool = False,
                          source_video: Path | str | None = None,
                          chs_subtitle: Path | str | None = None,
                          cht_subtitle: Path | str | None = None) -> dict:
        ctx = EpisodeFiles.discover(
            episode_dir,
            episode_id,
            config=self.config,
            source_video=source_video,
            chs_subtitle=chs_subtitle,
            cht_subtitle=cht_subtitle,
        )
        if not ctx.pure_mkv:
            missing_source = str(ctx.source_video_path or (ctx.episode_dir / f'{episode_id}.mkv'))
            return {"ok": False, "missing": [missing_source], "tracks": []}

        if smart:
            subs = MediaExtractor(ctx.episode_dir).extract_preferred_subtitles(
                ctx.pure_mkv,
                langs=self.config.subtitle_strategy.preferred,
                output_stem=episode_id,
            )
            tracks = subs.all_tracks() if subs else []
        else:
            tracks = MediaExtractor(ctx.episode_dir).extract_subtitle_tracks(
                ctx.pure_mkv,
                output_stem=episode_id,
            )

        return {
            "ok": True,
            "missing": [],
            "tracks": [str(track.output_path) for track in tracks],
        }

    def extract_audio(self, episode_dir: Path | str, episode_id: str,
                      source_video: Path | str | None = None,
                      chs_subtitle: Path | str | None = None,
                      cht_subtitle: Path | str | None = None) -> dict:
        ctx = EpisodeFiles.discover(
            episode_dir,
            episode_id,
            config=self.config,
            source_video=source_video,
            chs_subtitle=chs_subtitle,
            cht_subtitle=cht_subtitle,
        )
        if not ctx.pure_mkv:
            missing_source = str(ctx.source_video_path or (ctx.episode_dir / f'{episode_id}.mkv'))
            return {"ok": False, "missing": [missing_source], "tracks": []}

        tracks = MediaExtractor(ctx.episode_dir).extract_audio_tracks(ctx.pure_mkv, output_stem=episode_id)
        return {
            "ok": True,
            "missing": [],
            "tracks": [str(track.output_path) for track in tracks],
        }

    def extract_media(self, episode_dir: Path | str | None = None,
                      episodes: list[str] | None = None,
                      smart_subs: bool = True) -> dict[str, dict]:
        directory = Path(episode_dir) if episode_dir else self.work_dir
        extractor = MediaExtractor(directory)
        if episodes:
            targets = [directory / f"{ep}.mkv" for ep in episodes if (directory / f"{ep}.mkv").exists()]
        else:
            targets = extractor.find_digit_mkvs()

        results: dict[str, dict] = {}
        for target in targets:
            print(f"\n>>> 提取: {target.name}")
            audio = extractor.extract_audio_tracks(target)
            if smart_subs:
                subs = extractor.extract_preferred_subtitles(target, langs=self.config.subtitle_strategy.preferred)
                sub_tracks = subs.all_tracks() if subs else []
                subs_ok = subs is not None and subs.has_any
                if subs is None:
                    print(f"⚠️  [{target.stem}] 无字幕轨道！后续封装将需要外部字幕文件")
            else:
                sub_tracks = extractor.extract_subtitle_tracks(target)
                subs_ok = len(sub_tracks) > 0
            results[target.stem] = {
                "audio": audio,
                "subs": sub_tracks,
                "subs_ok": subs_ok,
            }
        return results

    def validate_workstation_subtitles(self, workstation: WorkstationConfig | Path | str, **kwargs) -> WorkstationBatchResult:
        ws = self._normalize_workstation(workstation, **kwargs)
        items: list[dict] = []
        outputs: list[str] = []
        missing: list[str] = []

        for ep_id in ws.effective_episode_ids:
            result = self.validate_subtitles(self._single_episode_dir(ws, ep_id), ep_id)
            items.append({"episode_id": ep_id, **result})
            outputs.extend(result.get("standardized", []))
            missing.extend([f"EP{ep_id}:{name}" for name in result.get("missing", [])])

        return WorkstationBatchResult(
            mode="collection",
            stage="validate_subtitles",
            ok=not missing,
            items=items,
            missing=missing,
            outputs=outputs,
            notes=["合集模式按集复用单集字幕校验逻辑"],
        )

    def encode_workstation_hevc(self, workstation: WorkstationConfig | Path | str, **kwargs) -> WorkstationBatchResult:
        ws = self._normalize_workstation(workstation, **kwargs)
        items: list[dict] = []
        outputs: list[str] = []
        missing: list[str] = []

        for ep_id in ws.effective_episode_ids:
            src = ws.source_video(ep_id)
            dst = ws.hevc_path(ep_id)
            if not src.exists():
                marker = f"EP{ep_id}:{src.name}"
                missing.append(marker)
                items.append({"episode_id": ep_id, "ok": False, "missing": [str(src)], "output": str(dst)})
                continue
            items.append({"episode_id": ep_id, "ok": True, "input": str(src), "output": str(dst)})
            outputs.append(str(dst))

        return WorkstationBatchResult(
            mode="collection",
            stage="encode_hevc",
            ok=not missing,
            items=items,
            missing=missing,
            outputs=outputs,
            notes=["合集模式阶段 3 以项目级目录布局推导 HEVC 输出路径"],
        )

    def build_release_batch(self, workstation: WorkstationConfig | Path | str, **kwargs) -> WorkstationBatchResult:
        ws = self._normalize_workstation(workstation, **kwargs)
        creator = TorrentCreator()
        items: list[dict] = []
        outputs: list[str] = []
        missing: list[str] = []

        for label, kind in (("HEVC", "hevc"), ("简日", "chs"), ("繁日", "cht")):
            pack_dir = ws.release_pack_dir(kind)
            torrent_path = ws.release_torrent_path(kind)
            exists = pack_dir.exists() and any(pack_dir.rglob("*"))
            item = {
                "label": label,
                "kind": kind,
                "pack_dir": str(pack_dir),
                "torrent_path": str(torrent_path),
                "ready": exists,
            }
            if exists:
                try:
                    item["plan"] = creator.build_plan(pack_dir, dst=torrent_path, v1_only=True).summary()
                except FileNotFoundError:
                    item["ready"] = False
            if item["ready"]:
                outputs.append(str(torrent_path))
            else:
                missing.append(f"{label}:{pack_dir}")
            items.append(item)

        return WorkstationBatchResult(
            mode="collection",
            stage="torrent_release_dirs",
            ok=not missing,
            items=items,
            missing=missing,
            outputs=outputs,
            notes=["合集模式按发布文件夹整体生成种子，而非逐集生成"],
        )

    def transcribe_episode(self, episode_dir: Path | str,
                           episode_id: str,
                           direct_model: str | None = None,
                           chunked_model: str | None = None,
                           manual_cuts: list[str] | None = None,
                           source_video: Path | str | None = None,
                           chs_subtitle: Path | str | None = None,
                           cht_subtitle: Path | str | None = None) -> dict:
        directory = Path(episode_dir)
        ctx = EpisodeFiles.discover(
            directory,
            episode_id,
            config=self.config,
            source_video=source_video,
            chs_subtitle=chs_subtitle,
            cht_subtitle=cht_subtitle,
        )
        audio_files = ctx.extracted_audio or sorted(directory.glob(f"{episode_id}_audiotracker*.aac"))
        if not audio_files:
            print(f"⚠️ 找不到 EP{episode_id} 的音轨文件")
            return {"ok": False, "missing": [f"{episode_id}_audio_*.aac"], "direct": None, "chunked": None}

        audio = audio_files[0]
        direct = None
        try:
            direct = self.transcriber.transcribe_direct(audio, model=direct_model or self.config.whisper_fast_model)
        except TranscriptionError as e:
            print(f"⚠️ 直接转录失败: {e}")

        chunked = None
        try:
            chunked = self.transcriber.transcribe_chunked(
                audio,
                model=chunked_model or self.config.whisper_detailed_model,
                manual_cuts=manual_cuts,
            )
        except TranscriptionError as e:
            print(f"⚠️ 分割转录失败: {e}")

        return {
            "ok": direct is not None or chunked is not None,
            "missing": [],
            "direct": direct,
            "chunked": chunked,
        }

    def encode_episode(self, episode_dir: Path | str, episode_id: str,
                       source_video: Path | str | None = None,
                       chs_subtitle: Path | str | None = None,
                       cht_subtitle: Path | str | None = None) -> Path:
        ctx = EpisodeFiles.discover(
            episode_dir,
            episode_id,
            config=self.config,
            source_video=source_video,
            chs_subtitle=chs_subtitle,
            cht_subtitle=cht_subtitle,
        )
        if not ctx.pure_mkv:
            raise FileNotFoundError(f"源文件不存在: {ctx.source_video_path or (Path(episode_dir) / f'{episode_id}.mkv')}")
        target = ctx.episode_dir / f"{episode_id}_HEVC10bit.mkv"
        return self.encoder.encode_hevc_vt(ctx.pure_mkv, dst=target)

    def validate_subtitles(self, episode_dir: Path | str, episode_id: str,
                           source_video: Path | str | None = None,
                           chs_subtitle: Path | str | None = None,
                           cht_subtitle: Path | str | None = None,
                           ensure_cht: bool = False,
                           converter: str | None = None,
                           api_url: str | None = None,
                           timeout: int | None = None,
                           regenerate_cht: bool | None = None) -> dict:
        ctx = EpisodeFiles.discover(
            episode_dir,
            episode_id,
            config=self.config,
            source_video=source_video,
            chs_subtitle=chs_subtitle,
            cht_subtitle=cht_subtitle,
        )
        if ensure_cht:
            ensure_result = self.validator.ensure_episode_subtitles(
                episode_dir,
                episode_id,
                source_video=source_video,
                chs_subtitle=chs_subtitle,
                cht_subtitle=cht_subtitle,
                converter=converter,
                api_url=api_url,
                timeout=timeout,
                regenerate_cht=regenerate_cht,
                standardize=True,
            )
            return {
                "all_ok": ensure_result["all_ok"],
                "standardized": [path.name for path in ensure_result["standardized"]],
                "missing": list(ensure_result["missing"]),
                "generated_cht": str(ensure_result["generated_cht"]) if ensure_result["generated_cht"] else None,
                "backed_up": [str(path) for path in ensure_result["backed_up"]],
                "validated": [str(path) for path in ensure_result["validated"]],
            }

        result = {"all_ok": True, "standardized": [], "missing": []}
        for sub_path in ctx.all_subs:
            violations = self.validator.validate_ass_header(sub_path)
            if violations:
                print(f"📝 标准化 {sub_path.name}: {list(violations.keys())}")
                self.validator.standardize_ass(sub_path)
                result["standardized"].append(sub_path.name)

        if not ctx.all_subs:
            result["all_ok"] = False
            missing_labels = []
            if chs_subtitle is not None:
                missing_labels.append(str(Path(chs_subtitle)))
            else:
                missing_labels.append(f"{episode_id}.chs&jpn.ass")
            if cht_subtitle is not None:
                missing_labels.append(str(Path(cht_subtitle)))
            else:
                missing_labels.append(f"{episode_id}.cht&jpn.ass")
            result["missing"] = missing_labels
            print("⚠️  未找到字幕文件！后续封装将需要:")
            for missing in result["missing"]:
                print(f"    - {missing}")

        return result

    def package_episode(self, episode_dir: Path | str, episode_id: str,
                        mkv_template: str | None = None,
                        chs_template: str | None = None,
                        cht_template: str | None = None,
                        prefix_chs: str | None = None,
                        prefix_cht: str | None = None,
                        project: ProjectNaming | None = None,
                        source_video: Path | str | None = None,
                        chs_subtitle: Path | str | None = None,
                        cht_subtitle: Path | str | None = None) -> list[Path]:
        packager = Packager(
            episode_dir,
            episode_id,
            self.config,
            source_video=source_video,
            chs_subtitle=chs_subtitle,
            cht_subtitle=cht_subtitle,
        )
        if not all([mkv_template, chs_template, cht_template]):
            return packager.package_expected(prefix_chs=prefix_chs, prefix_cht=prefix_cht, project=project)
        return packager.package_all(mkv_template, chs_template, cht_template)

    def upload_files_to_r2(self, file_paths: list[str | Path], remote_folder: str = "",
                           uploader: R2Uploader | None = None, **r2_kwargs) -> dict:
        uploader = uploader or R2Uploader(**r2_kwargs)
        uploaded_keys = uploader.upload_files(file_paths, remote_folder=remote_folder)
        return {
            "bucket_name": uploader.bucket_name,
            "remote_folder": remote_folder.strip("/"),
            "uploaded_keys": uploaded_keys,
        }

    def seed_torrents(self, files: list[Path], qb_host: str,
                      qb_user: str = "admin", qb_pass: str = "",
                      download_base: str = "/downloads") -> dict[str, bool]:
        return Publisher.seed_qbittorrent(
            host=qb_host,
            files=files,
            download_base=download_base,
            username=qb_user,
            password=qb_pass,
        )

    def process_episode(self, episode_dir: Path | str,
                        episode_id: str | None = None,
                        manual_cuts: dict | None = None,
                        direct_model: str | None = None,
                        chunked_model: str | None = None,
                        mkv_template: str | None = None,
                        chs_template: str | None = None,
                        cht_template: str | None = None,
                        prefix_chs: str | None = None,
                        prefix_cht: str | None = None,
                        project: ProjectNaming | None = None,
                        source_video: Path | str | None = None,
                        chs_subtitle: Path | str | None = None,
                        cht_subtitle: Path | str | None = None,
                        r2_prefix: str | None = None,
                        r2_uploader: R2Uploader | None = None,
                        qb_host: str | None = None,
                        skip_transcribe: bool = False,
                        skip_encode: bool = False,
                        skip_package: bool = False,
                        skip_upload: bool = False,
                        skip_seed: bool = False) -> dict:
        directory = Path(episode_dir)
        if episode_id is None:
            if source_video is not None:
                raise ValueError("传入 source_video 时必须显式指定 episode_id")
            mkvs = list(directory.glob("*.mkv"))
            digits = [m.stem for m in mkvs if re.match(r"^\d+$", m.stem) and "_HEVC10bit" not in m.stem]
            episode_id = digits[0] if digits else None
            if not episode_id:
                raise ValueError("无法推断 episode_id，请显式指定")

        cuts = (manual_cuts or {}).get(episode_id)
        result: dict = {"episode_id": episode_id, "stages": {}}
        timer = PipelineTimer(f"EP{episode_id}")

        print(f"\n{'=' * 50}\n📦 阶段 1: 素材提取 EP{episode_id}\n{'=' * 50}")
        with timer.stage("1.素材提取"):
            extract_result = {
                "audio": self.extract_audio(
                    directory,
                    episode_id,
                    source_video=source_video,
                    chs_subtitle=chs_subtitle,
                    cht_subtitle=cht_subtitle,
                ),
                "subtitles": self.extract_subtitles(
                    directory,
                    episode_id,
                    smart=True,
                    source_video=source_video,
                    chs_subtitle=chs_subtitle,
                    cht_subtitle=cht_subtitle,
                ),
            }
            result["stages"]["extract"] = extract_result

        if not skip_transcribe:
            print(f"\n{'=' * 50}\n🎙️  阶段 2: AI 转录 EP{episode_id}\n{'=' * 50}")
            with timer.stage("2.AI转录"):
                transcribe_result = self.transcribe_episode(
                    directory,
                    episode_id,
                    direct_model=direct_model,
                    chunked_model=chunked_model,
                    manual_cuts=cuts,
                    source_video=source_video,
                    chs_subtitle=chs_subtitle,
                    cht_subtitle=cht_subtitle,
                )
                result["stages"]["transcribe"] = {
                    "ok": transcribe_result["ok"],
                    "direct": str(transcribe_result["direct"]) if transcribe_result["direct"] else None,
                    "chunked": str(transcribe_result["chunked"]) if transcribe_result["chunked"] else None,
                }
        else:
            result["stages"]["transcribe"] = "skipped"

        if not skip_encode:
            print(f"\n{'=' * 50}\n🎬 阶段 3: HEVC 编码 EP{episode_id}\n{'=' * 50}")
            with timer.stage("3.HEVC编码"):
                hevc_path = self.encode_episode(
                    directory,
                    episode_id,
                    source_video=source_video,
                    chs_subtitle=chs_subtitle,
                    cht_subtitle=cht_subtitle,
                )
                result["stages"]["encode"] = str(hevc_path)
        else:
            result["stages"]["encode"] = "skipped"

        print(f"\n{'=' * 50}\n📝 阶段 4: 字幕校验 EP{episode_id}\n{'=' * 50}")
        with timer.stage("4.字幕校验"):
            result["stages"]["subtitles"] = self.validate_subtitles(
                directory,
                episode_id,
                source_video=source_video,
                chs_subtitle=chs_subtitle,
                cht_subtitle=cht_subtitle,
            )

        if not skip_package:
            print(f"\n{'=' * 50}\n📦 阶段 5: 封装 EP{episode_id}\n{'=' * 50}")
            with timer.stage("5.封装"):
                try:
                    pkg_files = self.package_episode(
                        directory,
                        episode_id,
                        mkv_template=mkv_template,
                        chs_template=chs_template,
                        cht_template=cht_template,
                        prefix_chs=prefix_chs,
                        prefix_cht=prefix_cht,
                        project=project,
                        source_video=source_video,
                        chs_subtitle=chs_subtitle,
                        cht_subtitle=cht_subtitle,
                    )
                    result["stages"]["package"] = [str(path) for path in pkg_files]
                except PackagingError as e:
                    result["stages"]["package"] = f"FAILED: {e}"
                    print(f"❌ 封装失败: {e}")

        if not skip_upload:
            pkg_files = result["stages"].get("package", [])
            upload_prefix = r2_prefix if r2_prefix is not None else getattr(self.config, "r2_prefix", "")
            if isinstance(pkg_files, list) and pkg_files:
                print(f"\n{'=' * 50}\n☁️  阶段 6: R2 上传 EP{episode_id}\n{'=' * 50}")
                with timer.stage("6.R2上传"):
                    upload_result = self.upload_files_to_r2(
                        [Path(path) for path in pkg_files],
                        remote_folder=upload_prefix,
                        uploader=r2_uploader,
                    )
                    result["stages"]["upload"] = upload_result

        if not skip_seed and qb_host:
            pkg_files = result["stages"].get("package", [])
            if isinstance(pkg_files, list) and pkg_files:
                print(f"\n{'=' * 50}\n🌐 阶段 7: 做种 EP{episode_id}\n{'=' * 50}")
                with timer.stage("7.做种"):
                    result["stages"]["seed"] = self.seed_torrents([Path(path) for path in pkg_files], qb_host)

        print(f"\n✨ EP{episode_id} 全流程完成")
        timer.summary()
        return result

    @staticmethod
    def _single_episode_dir(workstation: WorkstationConfig, episode_id: str) -> Path:
        candidate = workstation.root_dir / episode_id
        return candidate if candidate.is_dir() else workstation.raw_dir

    @staticmethod
    def _normalize_workstation(workstation: WorkstationConfig | Path | str, **kwargs) -> WorkstationConfig:
        if isinstance(workstation, WorkstationConfig):
            return workstation
        return WorkstationConfig(root_dir=Path(workstation), **kwargs)
