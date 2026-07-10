"""
AI 转录 — 两种方法 + 可更换模型

方法1: transcribe_direct()  — 整轨一次转录，适合快速出结果
方法2: transcribe_chunked() — 滑窗切片转录，适合长音频精细转录

两种方法都通过 model 参数指定任意 mlx-whisper 模型
输出文件名包含模型简称标记
"""

import re
import time
from pathlib import Path

from tqdm import tqdm


class TranscriptionError(Exception):
    """转录异常"""
    pass


def model_short_name(model_path: str) -> str:
    """从模型路径提取简短标识"""
    # mlx-community/whisper-large-v3-turbo → large-v3-turbo
    # mlx-community/kotoba-whisper-v2.0-8bit → kotoba-whisper-v2.0-8bit
    name = model_path.rstrip("/").split("/")[-1]
    for prefix in ("whisper-", "kotoba-"):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    return name


class Transcriber:
    """AI 转录处理器 — 可更换模型、两种转录方式"""

    def __init__(self,
                 model: str = "mlx-community/whisper-large-v3-turbo",
                 language: str = "ja",
                 chunk_sec: int = 240,
                 overlap_sec: int = 5,
                 export_format: str = "mp3",
                 output_root: str | Path = "./output_transcripts"):
        self.model = model
        self.language = language
        self.chunk_sec = chunk_sec
        self.overlap_sec = overlap_sec
        self.export_format = export_format
        self.output_root = Path(output_root)

    # ═══════════════════════════════════════════════
    # 方法1: 直接转录 — transcribe_direct()
    # ═══════════════════════════════════════════════

    def transcribe_direct(self, audio_path: Path,
                           model: str | None = None,
                           output_path: Path | None = None,
                           force: bool = False) -> Path | None:
        """
        方法1 — 直接转录：对完整音频做一次性转录，速度快

        输出文件名: {stem}_direct_{模型简称}.txt

        Parameters
        ----------
        audio_path : 音频文件路径
        model : 模型名，None=使用初始化时的默认模型
        output_path : 自定义输出路径，None=自动生成
        force : True=强制重新转录（忽略已存在文件）
        """
        import mlx_whisper

        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise TranscriptionError(f"音频文件不存在: {audio_path}")

        model_name = model or self.model
        mshort = model_short_name(model_name)

        if output_path is None:
            output_path = audio_path.parent / f"{audio_path.stem}_direct_{mshort}.txt"
        output_path = Path(output_path)

        if output_path.exists() and not force:
            print(f"⏩ 直接转录已存在: {output_path.name}")
            return output_path

        print(f"🤖 方法1-直接转录 | 模型: {mshort}")
        print(f"   输入: {audio_path.name} → 输出: {output_path.name}")
        start = time.time()

        try:
            result = mlx_whisper.transcribe(
                str(audio_path), path_or_hf_repo=model_name, language=self.language)
        except Exception as e:
            raise TranscriptionError(f"直接转录失败: {e}") from e

        output_path.write_text(
            "\n".join(seg["text"].strip() for seg in result.get("segments", [])),
            encoding="utf-8")
        print(f"✅ 直接转录完成 ({time.time()-start:.1f}s)")
        return output_path

    # ═══════════════════════════════════════════════
    # 方法2: 分割转录 — transcribe_chunked()
    # ═══════════════════════════════════════════════

    def transcribe_chunked(self, audio_path: Path,
                            model: str | None = None,
                            manual_cuts: list[str] | None = None,
                            output_dir: Path | None = None,
                            force: bool = False) -> Path | None:
        """
        方法2 — 分割转录：滑动窗口切片 → 逐片转录 → 合并，精细度高

        输出文件名: {stem}_chunked_{模型简称}_final.txt

        Parameters
        ----------
        audio_path : 音频文件路径
        model : 模型名，None=使用默认模型
        manual_cuts : 手动切点 ["10:00", "20:00"]，跳过 OP/ED
        output_dir : 工作目录，None=自动
        force : True=强制重新转录
        """
        from pydub import AudioSegment
        import mlx_whisper

        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise TranscriptionError(f"音频文件不存在: {audio_path}")

        model_name = model or self.model
        mshort = model_short_name(model_name)
        stem = audio_path.stem.split("_")[0]

        if output_dir is None:
            output_dir = self.output_root / f"work_{stem}_{mshort}"
        output_dir.mkdir(parents=True, exist_ok=True)

        final_path = output_dir / f"{audio_path.stem}_chunked_{mshort}_final.txt"
        if final_path.exists() and not force:
            print(f"⏩ 分割转录已存在: {final_path.name}")
            return final_path

        print(f"🤖 方法2-分割转录 | 模型: {mshort} | 切片{self.chunk_sec}s/重叠{self.overlap_sec}s")

        # 1. 切分
        chunks = self._split_audio(audio_path, output_dir, manual_cuts)
        print(f"🔪 切片完成: {len(chunks)} 个片段")

        # 2. 逐片转录
        self._transcribe_chunks(chunks, model_name, mlx_whisper)

        # 3. 合并
        self._merge_chunks(output_dir, final_path)
        return final_path

    # ═══════════════════════════════════════════════
    # 便捷：默认两轮组合
    # ═══════════════════════════════════════════════

    def transcribe_both(self, audio_path: Path,
                         fast_model: str = "mlx-community/whisper-large-v3-turbo",
                         detailed_model: str = "mlx-community/whisper-medium-mlx",
                         manual_cuts: list[str] | None = None) -> dict:
        """
        依次执行两种转录:
        - 方法1 (直接) 用 fast_model
        - 方法2 (分割) 用 detailed_model

        Returns {"direct": Path|None, "chunked": Path|None}
        """
        result: dict = {}
        try:
            result["direct"] = self.transcribe_direct(audio_path, model=fast_model)
        except TranscriptionError as e:
            print(f"⚠️ 直接转录失败: {e}"); result["direct"] = None

        try:
            result["chunked"] = self.transcribe_chunked(
                audio_path, model=detailed_model, manual_cuts=manual_cuts)
        except TranscriptionError as e:
            print(f"⚠️ 分割转录失败: {e}"); result["chunked"] = None

        return result

    # ═══════════════════════════════════════════════
    # 内部
    # ═══════════════════════════════════════════════

    def _split_audio(self, audio_path: Path, work_dir: Path,
                      manual_cuts: list[str] | None) -> list[Path]:
        from pydub import AudioSegment
        audio = AudioSegment.from_file(str(audio_path))
        total_ms = len(audio)

        cut_points = [0]
        if manual_cuts:
            cut_points.extend(self._timestamp_to_ms(t) for t in manual_cuts)
        cut_points.append(total_ms)
        cut_points = sorted(set(cut_points))

        all_chunks: list[Path] = []
        step_ms = (self.chunk_sec - self.overlap_sec) * 1000
        chunk_ms = self.chunk_sec * 1000

        for p_idx, (p_start, p_end) in enumerate(zip(cut_points[:-1], cut_points[1:])):
            part_dir = work_dir / f"part_{p_idx}"
            part_dir.mkdir(exist_ok=True)
            part_audio = audio[p_start:p_end]
            for idx, start_ms in enumerate(range(0, len(part_audio), step_ms)):
                end_ms = min(start_ms + chunk_ms, len(part_audio))
                out_path = part_dir / f"output_{idx}.{self.export_format}"
                if not out_path.exists():
                    part_audio[start_ms:end_ms].export(out_path, format=self.export_format)
                all_chunks.append(out_path)
                if end_ms == len(part_audio):
                    break
        return all_chunks

    def _transcribe_chunks(self, chunks: list[Path], model_name: str, mlx_whisper) -> None:
        for chunk_path in tqdm(chunks, desc="分割转录中"):
            txt_path = chunk_path.with_suffix(".txt")
            if txt_path.exists():
                continue
            result = mlx_whisper.transcribe(
                str(chunk_path), path_or_hf_repo=model_name, language=self.language)
            txt_path.write_text(
                "\n".join(seg["text"].strip() for seg in result.get("segments", [])),
                encoding="utf-8")
            time.sleep(0.3)

    def _merge_chunks(self, work_dir: Path, final_path: Path) -> None:
        all_text: list[str] = []
        part_dirs = sorted(
            (d for d in work_dir.iterdir() if d.is_dir() and d.name.startswith("part_")),
            key=lambda d: int(d.name.split("_")[1]))
        for p_dir in part_dirs:
            txt_files = sorted(
                p_dir.glob("output_*.txt"),
                key=lambda f: self._safe_sort_key(f))
            for tf in txt_files:
                lines = [l.strip() for l in tf.read_text(encoding="utf-8").splitlines() if l.strip()]
                all_text.extend(lines)
        final_path.write_text("\n".join(all_text) + "\n", encoding="utf-8")
        print(f"  >> 合并完成: {final_path}")

    @staticmethod
    def _safe_sort_key(f: Path) -> int:
        """安全排序键：从 output_*.txt 中提取数字索引，失败时返回 -1"""
        m = re.search(r"output_(\d+)", f.name)
        return int(m.group(1)) if m else -1

    @staticmethod
    def _timestamp_to_ms(ts: str) -> int:
        parts = list(map(int, ts.split(":")))
        if len(parts) == 2: return (parts[0] * 60 + parts[1]) * 1000
        elif len(parts) == 3: return (parts[0] * 3600 + parts[1] * 60 + parts[2]) * 1000
        return 0
