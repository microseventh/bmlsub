"""
流水线编排 — 高层编排所有模块，每个阶段独立可调用

典型用法:
    from bmlsub import Pipeline, PipelineConfig
    cfg = PipelineConfig(work_dir=".")
    pipe = Pipeline(cfg)
    pipe.process_episode(".")
"""

import re
from pathlib import Path

from ._backup import backup_if_exists
from .config import PipelineConfig
from .media import MediaExtractor, ExtractedTrack, PreferredSubs
from .progress import PipelineTimer
from .transcribe import Transcriber, TranscriptionError
from .encode import Encoder
from .subtitle import SubtitleValidator
from .package import Packager, PackagingError
from .transfer import Transfer, TransferError
from .publish import Publisher, PublishError


class Pipeline:
    """完整流水线编排器"""

    def __init__(self, config: PipelineConfig | None = None, **kwargs):
        self.config = config or PipelineConfig(**kwargs)
        self.work_dir = self.config.work_dir

        # 延迟初始化
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
            self._validator = SubtitleValidator(self.config.sub_standard)
        return self._validator

    # ═══════════════════════════════════════════════
    # 阶段 1：素材提取（智能字幕筛选）
    # ═══════════════════════════════════════════════

    def extract_media(self, episode_dir: Path | str | None = None,
                       episodes: list[str] | None = None,
                       smart_subs: bool = True) -> dict[str, dict]:
        """
        提取指定集数的音轨和字幕

        Parameters
        ----------
        episode_dir : 集数目录
        episodes : 集数列表 ["01", "02"]，None=自动查找
        smart_subs : True=智能筛选（中/英/日优先），False=全部提取

        Returns
        -------
        {"01": {"audio": [...], "subs": PreferredSubs|list, "subs_ok": bool}}
        """
        d = Path(episode_dir) if episode_dir else self.work_dir
        extractor = MediaExtractor(d)

        if episodes:
            targets = [d / f"{ep}.mkv" for ep in episodes if (d / f"{ep}.mkv").exists()]
        else:
            targets = extractor.find_digit_mkvs()

        results: dict[str, dict] = {}
        for t in targets:
            print(f"\n>>> 提取: {t.name}")
            audio = extractor.extract_audio_tracks(t)

            if smart_subs:
                subs = extractor.extract_preferred_subtitles(t)
                subs_ok = subs is not None and subs.has_any
                if subs is None:
                    print(f"⚠️  [{t.stem}] 无字幕轨道！后续封装将需要外部字幕文件")
            else:
                subs = extractor.extract_subtitle_tracks(t)
                subs_ok = len(subs) > 0

            results[t.stem] = {"audio": audio, "subs": subs, "subs_ok": subs_ok}
        return results

    # ═══════════════════════════════════════════════
    # 阶段 2：AI 转录（两种方法）
    # ═══════════════════════════════════════════════

    def transcribe_episode(self, episode_dir: Path | str,
                            episode_id: str,
                            direct_model: str | None = None,
                            chunked_model: str | None = None,
                            manual_cuts: list[str] | None = None) -> dict:
        """
        对单集执行转录

        Parameters
        ----------
        direct_model : 直接转录用的模型，None=默认 fast_model
        chunked_model : 分割转录用的模型，None=默认 detailed_model
        manual_cuts : 手动切点列表

        Returns
        -------
        {"direct": Path|None, "chunked": Path|None}
        """
        d = Path(episode_dir)
        audio_files = sorted(d.glob(f"{episode_id}_audio_*.aac"))
        if not audio_files:
            audio_files = sorted(d.glob(f"{episode_id}_audiotracker*.aac"))

        if not audio_files:
            print(f"⚠️ 找不到 EP{episode_id} 的音轨文件")
            return {"direct": None, "chunked": None}

        audio = audio_files[0]

        # 方法1: 直接转录
        direct = None
        try:
            direct = self.transcriber.transcribe_direct(
                audio, model=direct_model or self.config.whisper_fast_model)
        except TranscriptionError as e:
            print(f"⚠️ 直接转录失败: {e}")

        # 方法2: 分割转录
        chunked = None
        try:
            chunked = self.transcriber.transcribe_chunked(
                audio, model=chunked_model or self.config.whisper_detailed_model,
                manual_cuts=manual_cuts)
        except TranscriptionError as e:
            print(f"⚠️ 分割转录失败: {e}")

        return {"direct": direct, "chunked": chunked}

    # ═══════════════════════════════════════════════
    # 阶段 3：HEVC 编码
    # ═══════════════════════════════════════════════

    def encode_episode(self, episode_dir: Path | str,
                        episode_id: str) -> Path:
        """单集 HEVC VideoToolbox 硬件编码"""
        d = Path(episode_dir)
        src = d / f"{episode_id}.mkv"
        if not src.exists():
            raise FileNotFoundError(f"源文件不存在: {src}")
        return self.encoder.encode_hevc_vt(src)

    def encode_hevc_batch(self, episode_dir: Path | str,
                           episodes: list[str] | None = None) -> list[Path]:
        """批量 HEVC 编码"""
        d = Path(episode_dir)
        extractor = MediaExtractor(d)
        targets = [d / f"{ep}.mkv" for ep in episodes] if episodes \
                   else [p for p in extractor.find_digit_mkvs()
                         if re.match(r'^\d+$', p.stem)]
        return [self.encoder.encode_hevc_vt(t) for t in targets]

    # ═══════════════════════════════════════════════
    # 阶段 4：字幕校验与标准化
    # ═══════════════════════════════════════════════

    def validate_subtitles(self, episode_dir: Path | str,
                            episode_id: str) -> dict:
        """单集字幕校验 + ASS 头标准化（对所有匹配到的字幕文件）"""
        d = Path(episode_dir)
        pkg = Packager(d, episode_id, self.config)
        files = pkg.get_available_files()

        result = {"all_ok": True, "standardized": [], "missing": []}

        for sub_path in files["all_subs"]:
            violations = self.validator.validate_ass_header(sub_path)
            if violations:
                print(f"📝 标准化 {sub_path.name}: {list(violations.keys())}")
                self.validator.standardize_ass(sub_path)
                result["standardized"].append(sub_path.name)

        if not files["all_subs"]:
            result["all_ok"] = False
            result["missing"] = [f"{episode_id}.chs&jpn.ass", f"{episode_id}.cht&jpn.ass"]
            print(f"⚠️  未找到字幕文件！后续封装将需要:")
            for m in result["missing"]:
                print(f"    - {m}")

        return result

    # ═══════════════════════════════════════════════
    # 阶段 5：封装（自动匹配字幕）
    # ═══════════════════════════════════════════════

    def package_episode(self, episode_dir: Path | str, episode_id: str,
                         mkv_template: str, chs_template: str,
                         cht_template: str) -> list[Path]:
        """
        mkvmerge 封装 + ffmpeg 硬压（自动匹配字幕文件）
        """
        d = Path(episode_dir)
        packager = Packager(d, episode_id, self.config)

        # 先检查字幕
        files = packager.get_available_files()
        if not files["all_subs"]:
            raise PackagingError(
                f"EP{episode_id}: 未找到任何字幕文件！"
                f"请提供 {episode_id}.chs&jpn.ass 或 {episode_id}.cht&jpn.ass"
            )

        return packager.package_all(mkv_template, chs_template, cht_template)

    # ═══════════════════════════════════════════════
    # 阶段 6+7：传输 + 发布
    # ═══════════════════════════════════════════════

    def transfer_files(self, file_paths: list[str | Path],
                        ssh_config: dict,
                        remote_dir: str = "/opt/qb/downloads") -> bool:
        """croc + SSH 安全传输"""
        transfer = Transfer(ssh_config, remote_dir)
        return transfer.send_files(file_paths)

    def seed_torrents(self, files: list[Path],
                       qb_host: str,
                       qb_user: str = "admin",
                       qb_pass: str = "",
                       download_base: str = "/downloads") -> dict[str, bool]:
        """qBittorrent 做种"""
        return Publisher.seed_qbittorrent(
            host=qb_host, files=files,
            download_base=download_base,
            username=qb_user, password=qb_pass,
        )

    # ═══════════════════════════════════════════════
    # 一键全流程
    # ═══════════════════════════════════════════════

    def process_episode(self, episode_dir: Path | str,
                         episode_id: str | None = None,
                         manual_cuts: dict | None = None,
                         direct_model: str | None = None,
                         chunked_model: str | None = None,
                         mkv_template: str | None = None,
                         chs_template: str | None = None,
                         cht_template: str | None = None,
                         ssh_config: dict | None = None,
                         remote_dir: str = "/opt/qb/downloads",
                         qb_host: str | None = None,
                         skip_transcribe: bool = False,
                         skip_encode: bool = False,
                         skip_package: bool = False,
                         skip_transfer: bool = False,
                         skip_seed: bool = False,
                         ) -> dict:
        """
        单集全流程处理

        Parameters
        ----------
        episode_dir : 集数所在目录
        episode_id : 集数编号，None=自动推断
        manual_cuts : {"01": ["10:00", "20:00"]} 手动切点
        direct_model : 直接转录模型，None=默认
        chunked_model : 分割转录模型，None=默认
        """
        d = Path(episode_dir)
        if episode_id is None:
            mkvs = list(d.glob("*.mkv"))
            digits = [m.stem for m in mkvs
                      if re.match(r'^\d+$', m.stem) and "_HEVC10bit" not in m.stem]
            episode_id = digits[0] if digits else None
            if not episode_id:
                raise ValueError("无法推断 episode_id，请显式指定")

        cuts = (manual_cuts or {}).get(episode_id)
        result: dict = {"episode_id": episode_id, "stages": {}}
        timer = PipelineTimer(f"EP{episode_id}")

        # ── 1. 提取（智能字幕筛选） ──
        print(f"\n{'='*50}\n📦 阶段 1: 素材提取 EP{episode_id}\n{'='*50}")
        with timer.stage("1.素材提取"):
            extractor = MediaExtractor(d)
            src = d / f"{episode_id}.mkv"
            audio = extractor.extract_audio_tracks(src)
            subs = extractor.extract_preferred_subtitles(src)

            result["stages"]["extract"] = {
                "audio_count": len(audio),
                "subs_summary": subs.summary() if subs else "无字幕",
                "subs_ok": subs is not None and subs.has_any,
            }
            if subs is None:
                print(f"⚠️  [{episode_id}] 此集无内封字幕！后续封装请准备外部字幕文件")
            elif not subs.has_any:
                print(f"⚠️  [{episode_id}] 字幕提取为空！")

        # ── 2. 转录（两种方法） ──
        if not skip_transcribe:
            print(f"\n{'='*50}\n🎙️  阶段 2: AI 转录 EP{episode_id}\n{'='*50}")
            with timer.stage("2.AI转录"):
                transcribe_result = self.transcribe_episode(
                    d, episode_id,
                    direct_model=direct_model,
                    chunked_model=chunked_model,
                    manual_cuts=cuts,
                )
                result["stages"]["transcribe"] = {
                    k: str(v) if v else None for k, v in transcribe_result.items()
                }
        else:
            result["stages"]["transcribe"] = "skipped"

        # ── 3. HEVC 编码 ──
        if not skip_encode:
            print(f"\n{'='*50}\n🎬 阶段 3: HEVC 编码 EP{episode_id}\n{'='*50}")
            with timer.stage("3.HEVC编码"):
                hevc_path = self.encode_episode(d, episode_id)
                result["stages"]["encode"] = str(hevc_path)
        else:
            result["stages"]["encode"] = "skipped"

        # ── 4. 字幕校验 ──
        print(f"\n{'='*50}\n📝 阶段 4: 字幕校验 EP{episode_id}\n{'='*50}")
        with timer.stage("4.字幕校验"):
            sub_status = self.validate_subtitles(d, episode_id)
            result["stages"]["subtitles"] = sub_status

        # ── 5. 封装 ──
        if not skip_package and mkv_template:
            print(f"\n{'='*50}\n📦 阶段 5: 封装 EP{episode_id}\n{'='*50}")
            pkg = Packager(d, episode_id, self.config)
            with timer.stage("5.封装"):
                try:
                    pkg_files = pkg.package_all(mkv_template,
                        chs_template or mkv_template.replace(".mkv", ".mp4"),
                        cht_template or mkv_template.replace(".mkv", ".mp4"))
                    result["stages"]["package"] = [str(f) for f in pkg_files]
                except PackagingError as e:
                    result["stages"]["package"] = f"FAILED: {e}"
                    print(f"❌ 封装失败: {e}")

        # ── 6. 传输 ──
        if not skip_transfer and ssh_config:
            pkg_files = result["stages"].get("package", [])
            if isinstance(pkg_files, list) and pkg_files:
                print(f"\n{'='*50}\n📡 阶段 6: 传输 EP{episode_id}\n{'='*50}")
                with timer.stage("6.传输"):
                    self.transfer_files([Path(f) for f in pkg_files], ssh_config, remote_dir)

        # ── 7. 做种 ──
        if not skip_seed and qb_host:
            pkg_files = result["stages"].get("package", [])
            if isinstance(pkg_files, list) and pkg_files:
                print(f"\n{'='*50}\n🌐 阶段 7: 做种 EP{episode_id}\n{'='*50}")
                with timer.stage("7.做种"):
                    self.seed_torrents([Path(f) for f in pkg_files], qb_host)

        print(f"\n✨ EP{episode_id} 全流程完成")
        timer.summary()
        return result
