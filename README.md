# bmlsub — BML 动漫字幕制作与发布流水线

bmlsub 是 **Billion Meta Lab (BML)** 的动漫字幕制作与发布工具包，覆盖从 MKV 素材提取到 BT 发布的全流程。

## 安装

### pip 安装

```bash
# 核心功能（提取、编码、封裝、字幕校驗、R2 上傳）
pip install bml-subpro

# 包含 AI 转录（仅 Apple Silicon）
pip install "bml-subpro[transcribe]"

# 包含种子生成（需系統 libtorrent）
pip install "bml-subpro[torrent]"

# 全部功能
pip install "bml-subpro[all]"
```

### 系统依赖

| 工具 | 安装方式 | 用途 | 必需 |
|------|---------|------|------|
| `ffmpeg` | `brew install ffmpeg` / `apt install ffmpeg` | 音视频编解码、字幕提取/烧录 | ✅ |
| `mkvtoolnix` | `brew install mkvtoolnix` / `apt install mkvtoolnix` | MKV 封装、元数据清理 | ✅ |
| `libtorrent-rasterbar` | `brew install libtorrent-rasterbar` / `apt install python3-libtorrent` | BT 种子生成（[torrent] extra） | 可选 |
| `croc` | `brew install croc` / `curl https://getcroc.schollz.com \| bash` | P2P 加密传输 | 可选 |
| `rclone` | `brew install rclone` / `apt install rclone` | R2 → 服务器同步（服务器端） | 可选 |

### 开发安装

```bash
git clone https://github.com/billion-metalab/bmlsub.git
cd bmlsub
pip install -e ".[all]"
```

> **完整可运行示例见 [01/test_pyjs.ipynb](01/test_pyjs.ipynb)** — 包含从头到尾的完整流水线代码，可作为实际项目模板参考。

---

## 快速开始

### 项目模板

换项目时只需改模板部分的几行常量。后续每个阶段独立运行、前置条件检查、自动备份旧文件。

```python
from bmlsub import (
    MediaExtractor, Transcriber, Encoder, SubtitleValidator,
    TorrentCreator, Transfer, R2Uploader, RemoteSeeder, Publisher,
    PRESET_HEVC_VT_DEFAULT, PRESET_X264_SLOW, SUB_STANDARD_HD,
    PipelineTimer, backup_if_exists,
    product_path, product_torrent_path, scan_products,
)
from pathlib import Path
import subprocess, fnmatch

EP_DIR = Path(".").resolve()
EP_ID = "01"
SRC = EP_DIR / f"{EP_ID}.mkv"

# ── 项目模板（换项目时只改这几行）──
PREFIX_CHS = "[Billion Meta Lab] 作品名"
PREFIX_CHT = "[Billion Meta Lab] 作品名（繁体）"
R2_FOLDER = "作品名"
SSH_ALIAS = "my-server"
REMOTE_DIR = "/path/to/downloads"
BGM_ID = 572613

# 产物路径（由 product_path 统一生成）
mp4_chs  = product_path(EP_DIR, EP_ID, "mp4_chs",  PREFIX_CHS, PREFIX_CHT)
mp4_cht  = product_path(EP_DIR, EP_ID, "mp4_cht",  PREFIX_CHS, PREFIX_CHT)
mkv_hevc = product_path(EP_DIR, EP_ID, "mkv_hevc", PREFIX_CHS, PREFIX_CHT)
```

`mp4_chs` `mp4_cht` `mkv_hevc` 分别对应三个标准产物路径，后续所有阶段通过它们做前置检查和生成。

---

## 完整流水线教程

> 以下每个阶段独立可运行，中间产物会被自动跳过（已有则不再重复生成），覆盖旧文件前自动备份到 `_backup/` 目录。

### 阶段 1：提取字幕 & 音轨

从 MKV 中提取所有音轨和字幕轨，支持智能筛选（中/英/日优先）。

```python
extractor = MediaExtractor(EP_DIR)

# 列出字幕流（不提取，供预览）
sub_list = extractor.list_subtitle_streams(SRC)
for s in sub_list:
    print(f"  [{s.index}] lang={s.language} title='{s.title}' codec={s.codec_name}")

# 提取所有字幕轨 → {stem}_sub_{lang}_{index}.ass
all_subs = extractor.extract_subtitle_tracks(SRC)

# 提取所有音轨 → {stem}_audio_{lang}_{index}.aac
audio_tracks = extractor.extract_audio_tracks(SRC)

# 智能筛选提取：中文 > 英文 > 日文 > 其他（按优先级分类）
preferred = extractor.extract_preferred_subtitles(SRC)
if preferred:
    print(preferred.summary())       # "中文 2 条; 日文 1 条"
    for t in preferred.all_tracks(): # 遍历所有筛选结果
        print(t.output_path)
```

**关键参数说明：**

- `MediaExtractor(work_dir)`：`work_dir` 为工作目录，提取产物输出到此目录。
- `list_subtitle_streams(video)` → `list[SubtitleInfo]`：列出字幕流元信息，不提取。`SubtitleInfo` 含 `index`（流索引）、`language`（语言代码如 `chi`/`eng`/`jpn`）、`title`（轨道标题）、`codec_name`（编码如 `subrip`/`ass`）。
- `extract_audio_tracks(video, progress=None)`：提取所有音轨为 AAC 192k，文件名 `{stem}_audio_{lang}_{index}.aac`。`progress` 可选传入 tqdm 兼容对象显示进度。返回 `list[ExtractedTrack]`。
- `extract_subtitle_tracks(video)`：提取所有字幕轨为 ASS 格式，文件名 `{stem}_sub_{lang}_{index}.ass`。
- `extract_preferred_subtitles(video, langs=None)`：智能筛选。`langs` 自定义语言优先级，默认 `["chi", "eng", "jpn"]`。若只有一种语言则全量提取。返回 `PreferredSubs` 或 `None`（无字幕）。
- `extract_all(video)` → `(音频列表, 字幕列表)`：一键全量提取。
- `extract_smart(video)` → `(音频列表, 智能筛选结果)`：音轨全量 + 字幕智能筛选。
- `get_audio_track(video, index=0)` → `Path | None`：快速获取第 N 条音轨路径。

**数据类：**

- `ExtractedTrack(index, codec_type, language, title, codec_name, output_path)`：提取后的轨道信息，`codec_type` 为 `"audio"` 或 `"subtitle"`。
- `PreferredSubs(chi, eng, jpn, other)`：按语言分类的提取结果。属性：`.total_count`、`.has_any`；方法：`.all_tracks()` 返回全部列表、`.summary()` 返回中文摘要字符串。
- `SubtitleInfo(index, language, title, codec_name)`：字幕轨道元信息。

### 阶段 2：AI 转录（MLX Whisper）

> **macOS 专用：** MLX Whisper 仅支持 macOS（Apple Silicon 原生加速）。建议先用 `resolve_model()` 确认最佳模型。

支持两种转录方式：直接转录（快速）和分割转录（精细，适合长音频）。

```python
audio_files = sorted(EP_DIR.glob(f"{EP_ID}_audio_*.aac"))
if not audio_files:
    print("⚠️ 未找到音轨文件，跳过转录")
else:
    audio_path = audio_files[0]

    # ── 步骤 0：模型选择（推荐先执行）──
    from bmlsub import resolve_model, print_model_guide

    # 打印完整指引（当前平台 / 已缓存 / 推荐模型列表）
    print_model_guide(language="ja")

    # 自动选择最佳模型 + 检查是否已下载
    info = resolve_model(language="ja")
    print(f"推荐模型: {info.model_id}")
    print(f"已下载: {'✅' if info.available else '❌ 需下载'}")

    # 如未下载：手动下载（MLX 模型首次调用时也会自动下载）
    # if not info.available:
    #     from bmlsub import download_model
    #     download_model(info.model_id)

    # ── 步骤 1：创建 Transcriber ──
    transcriber = Transcriber(
        model=info.model_id,   # 使用解析后的模型
        language="ja",
        chunk_sec=240,         # 切片长度（秒）
        overlap_sec=5,         # 切片重叠（秒）
        export_format="mp3",   # 切片导出格式
        output_root="./output_transcripts",
    )

    # 方法 1：直接转录 — 整轨一次性转录，速度快
    direct = transcriber.transcribe_direct(
        audio_path,
        model="mlx-community/whisper-large-v3-turbo",
        # output_path=...   # 自定义输出路径
        # force=False,       # True=强制覆盖
    )

    # 方法 2：分割转录 — 滑动窗口切片 → 逐片转录 → 合并，精细度高
    chunked = transcriber.transcribe_chunked(
        audio_path,
        model="mlx-community/whisper-medium-mlx",
        manual_cuts=["1:30", "22:00"],  # 手动切点（跳过 OP/ED）
        # output_dir=...    # 自定义工作目录
        # force=False,       # True=强制覆盖
    )

    # 便捷方法：两种方法依次执行
    both = transcriber.transcribe_both(
        audio_path,
        fast_model="mlx-community/whisper-large-v3-turbo",
        detailed_model="mlx-community/whisper-medium-mlx",
        manual_cuts=["1:30", "22:00"],
    )
    # → {"direct": Path | None, "chunked": Path | None}
```

**关键参数说明：**

**`Transcriber` 构造函数：**


| 参数            | 类型         | 默认值                                   | 说明                                 |
| --------------- | ------------ | ---------------------------------------- | ------------------------------------ |
| `model`         | `str`        | `"mlx-community/whisper-large-v3-turbo"` | 默认模型（HF repo 路径）             |
| `language`      | `str`        | `"ja"`                                   | 音频语言代码                         |
| `chunk_sec`     | `int`        | `240`                                    | 分割转录时每段切片长度（秒）         |
| `overlap_sec`   | `int`        | `5`                                      | 相邻切片重叠时长（秒），防止边界截断 |
| `export_format` | `str`        | `"mp3"`                                  | 切片导出音频格式                     |
| `output_root`   | `str | Path` | `"./output_transcripts"`                 | 转录结果输出根目录                   |

**`transcribe_direct(audio_path, model=None, output_path=None, force=False)`：**

- `audio_path`：音频文件路径（必需）。
- `model`：覆盖默认模型，`None` 使用构造时的 `self.model`。
- `output_path`：自定义输出路径，`None` 自动生成 `{stem}_direct_{模型简称}.txt`。
- `force`：`True` 强制重新转录，忽略已存在的输出文件。
- 返回 `Path` 指向转录文本文件；异常抛出 `TranscriptionError`。

**`transcribe_chunked(audio_path, model=None, manual_cuts=None, output_dir=None, force=False)`：**

- `audio_path`：音频文件路径（必需）。
- `model`：覆盖默认模型。
- `manual_cuts`：手动切点列表，如 `["10:00", "20:00"]`，用于跳过 OP/ED 段落。每个切点格式为 `"MM:SS"` 或 `"HH:MM:SS"`。
- `output_dir`：切片工作目录，`None` 自动创建 `output_transcripts/work_{stem}_{模型简称}/`。
- `force`：`True` 强制重新转录。
- 内部流程：音频分段 → 滑窗切片 → 逐片 `mlx_whisper.transcribe()` → 文本合并。
- 返回 `Path` 指向最终合并文本 `{stem}_chunked_{模型简称}_final.txt`。

**`transcribe_both(audio_path, fast_model, detailed_model, manual_cuts=None)`：**

- 依次执行直接转录（用 `fast_model`）和分割转录（用 `detailed_model`）。
- 单个方法失败不会中断另一个。
- 返回 `{"direct": Path|None, "chunked": Path|None}`。

**推荐模型：**


| 模型                                     | 速度 | 精度 | 适用场景                  |
| ---------------------------------------- | ---- | ---- | ------------------------- |
| `mlx-community/whisper-large-v3-turbo`   | 快   | 高   | 直接转录                  |
| `mlx-community/whisper-medium-mlx`       | 中   | 中   | 分割转录                  |
| `mlx-community/kotoba-whisper-v2.0-8bit` | 快   | 最高 | 日语专用（需要自行转mlx） |

**辅助函数：**

- `model_short_name(model_path)`：从完整 HF 路径提取简短模型名。如 `"mlx-community/whisper-large-v3-turbo"` → `"large-v3-turbo"`。

### 阶段 3：HEVC VideoToolbox 硬件编码

Mac 平台使用 VideoToolbox 进行 HEVC 10bit 硬件加速编码。

```python
encoder = Encoder(PRESET_HEVC_VT_DEFAULT, PRESET_X264_SLOW)

if SRC.exists():
    hevc_path = encoder.encode_hevc_vt(SRC)
    # → {id}_HEVC10bit.mkv

    # 验证编码产物元数据是否干净
    issues = encoder.verify_metadata_clean(hevc_path)
    if not issues:
        print("✅ 元数据完全干净！")
    else:
        print(f"⚠️ 残留言标签: {issues}")
```

**关键参数说明：**

**`Encoder` 构造函数：**


| 参数          | 类型                  | 默认值                   | 说明                       |
| ------------- | --------------------- | ------------------------ | -------------------------- |
| `hevc_preset` | `EncodePreset | None` | `PRESET_HEVC_VT_DEFAULT` | HEVC VideoToolbox 编码预设 |
| `x264_preset` | `EncodePreset | None` | `PRESET_X264_SLOW`       | x264 软件编码预设          |

**`encode_hevc_vt(src, dst=None, audio_streams=None, strip_metadata=True)` → `Path`：**

- `src`：源 MKV 文件路径。
- `dst`：输出路径。默认 `{src.stem}_HEVC10bit.mkv`。
- `audio_streams`：保留的音轨流索引列表，如 `[0, 2]`。`None` = 保留全部音轨。
- `strip_metadata`：编码后是否清理元数据（`mkvpropedit --delete-track-statistics-tags` + `--tags all:`）。`True` 为默认。
- 内部流程：ffmpeg HEVC VideoToolbox 编码 → `mkvpropedit` 深度清理 → 回退 ffmpeg 流拷贝（若 mkvpropedit 不可用）。

**`encode_x264(src, dst, ass_subtitle=None, preset=None)` → `Path`：**

- `src`：源视频路径。
- `dst`：输出 `.mp4` 路径（必需）。
- `ass_subtitle`：ASS 字幕文件路径，`None` = 不烧录。
- `preset`：覆盖默认 x264 预设。

**`strip_metadata(video_path)` → `Path`：**

- 单独执行元数据深度清理。先用 `mkvpropedit`，失败则回退 `ffmpeg -c copy`。

**`verify_metadata_clean(video_path)` → `dict`：**

- 返回残留的源泄露标签。空 `dict` = 完全干净。
- 检查项包括：`_STATISTICS_*` 标签、`BPS`、`DURATION`、`NUMBER_OF_FRAMES`、`title`、`HANDLER_NAME`、`VENDOR_ID` 等。

### 阶段 4：字幕校验 & ASS 头部标准化

清理阶段 1 提取的原始字幕，只保留制作组字幕，然后校验并标准化 ASS 头部。

```python
# 第一步：清理多余字幕，只保留制作组字幕文件
KEEP_PATTERNS = ("*.chs&jpn.ass", "*.cht&jpn.ass", "*.chs.ass", "*.cht.ass")

for f in sorted(EP_DIR.glob("*.ass")):
    if not any(fnmatch.fnmatch(f.name, p) for p in KEEP_PATTERNS):
        f.unlink()

# 第二步：校验并标准化 ASS 头部
validator = SubtitleValidator(SUB_STANDARD_HD)

for ass in sorted(EP_DIR.glob("*.ass")):
    violations = validator.validate_ass_header(ass)
    if violations:
        print(f"  {ass.name}: 不合规字段 → {list(violations.keys())}")
        validator.standardize_ass(ass)
    else:
        print(f"  ✅ {ass.name} 已合规")
```

**关键参数说明：**

**`SubtitleValidator` 构造函数：**


| 参数       | 类型                      | 默认值            | 说明                                    |
| ---------- | ------------------------- | ----------------- | --------------------------------------- |
| `standard` | `SubtitleStandard | None` | `SUB_STANDARD_HD` | 字幕规范，决定校验的 ASS 头部字段目标值 |

**`SubtitleStandard` 数据类：**


| 参数                       | 类型  | 默认值     | 说明                                    |
| -------------------------- | ----- | ---------- | --------------------------------------- |
| `play_res_x`               | `int` | `1920`     | 播放分辨率宽                            |
| `play_res_y`               | `int` | `1080`     | 播放分辨率高                            |
| `color_matrix`             | `str` | `"TV.709"` | 色彩矩阵（ASS 头部`YCbCr Matrix`）      |
| `script_type`              | `str` | `"v4.00+"` | 脚本类型（`ScriptType`）                |
| `wrap_style`               | `int` | `0`        | 换行风格（`WrapStyle`）                 |
| `scaled_border_and_shadow` | `str` | `"yes"`    | 边框阴影缩放（`ScaledBorderAndShadow`） |

**`validate_ass_header(ass_path)` → `dict[str, str]`：**

- 检查 ASS 文件 `[Script Info]` 段是否与标准一致。
- 返回不合规字段的 `{字段名: 当前值}` 字典。空 `dict` = 完全合规。

**`standardize_ass(ass_path, output_path=None)` → `Path`：**

- 就地修正 ASS 头部（先备份到 `_backup/`）。
- `output_path`：`None` = 覆盖原文件；指定路径 = 输出到新文件。
- 不修改 `[V4+ Styles]` 和 `[Events]` 段的内容。

**`check_subtitle_exists(episode_dir, episode_id, sub_type)` → `Path | None`：**

- 检查 `{episode_id}.{sub_type}&jpn.ass` 是否存在。`sub_type` 为 `"chs"` 或 `"cht"`。

**`validate_for_episode(episode_dir, episode_id)` → `dict`：**

- 单集完整字幕状态校验。返回 `{"chs": {...}, "cht": {...}, "all_ok": bool}`。

**`standardize_extracted_subs(episode_dir, episode_id)` → `list[Path]`：**

- 批量标准化 `{ep_id}_sub_*.ass` 原始提取文件。

### 阶段 5：x264 软编码 + ASS 硬字幕烧录

将 ASS 字幕通过 ffmpeg `ass` 滤镜烧录到视频中，输出简体/繁体两个 MP4。

```python
chs_sub = EP_DIR / f"{EP_ID}.chs&jpn.ass"
cht_sub = EP_DIR / f"{EP_ID}.cht&jpn.ass"

if chs_sub.exists() or cht_sub.exists():
    from bmlsub import PRESET_X264_SLOW
    encode_params = (
        PRESET_X264_SLOW.to_ffmpeg_video_params()
        + PRESET_X264_SLOW.to_ffmpeg_audio_params()
        + ["-map_metadata", "-1", "-fflags", "+bitexact", "-flags:v", "+bitexact"]
    )

    for sub_path, out_path, label in [
        (chs_sub, mp4_chs, "简体中文"),
        (cht_sub, mp4_cht, "繁體中文"),
    ]:
        if sub_path.exists():
            backup_if_exists(out_path)
            subprocess.run([
                "ffmpeg", "-y", "-i", str(SRC),
                "-vf", f"ass='{sub_path.absolute()}'",
            ] + encode_params + [str(out_path)], check=True)
```

> 也可使用 `Packager.ffmpeg_hardsub_encode()` 或 `Encoder.encode_x264()` 封装此流程。

### 阶段 6：mkvmerge 封装

将 HEVC 视频 + 简繁 ASS 字幕 + 字体附件封装为最终发布 MKV。

```python
hevc_mkv = EP_DIR / f"{EP_ID}_HEVC10bit.mkv"
fonts = []
for ext in ("*.ttf", "*.otf", "*.ttc"):
    fonts.extend(EP_DIR.glob(ext))

if hevc_mkv.exists() and chs_sub.exists() and cht_sub.exists() and fonts:
    backup_if_exists(mkv_hevc)

    cmd = ["mkvmerge", "-o", str(mkv_hevc), str(hevc_mkv)]
    cmd += ["--language", "0:chi", "--track-name", "0:简体中文+日语",
            "--default-track", "0:yes", str(chs_sub)]
    cmd += ["--language", "0:chi", "--track-name", "0:繁體中文+日语",
            "--default-track", "0:no", str(cht_sub)]
    for font in fonts:
        cmd += ["--attachment-mime-type", "application/x-truetype-font",
                "--attach-file", str(font)]

    subprocess.run(cmd, check=True, timeout=600)
```

> 也可使用 `Packager.mkvmerge_package()` 封装此流程，自动匹配字幕和字体文件。

### 阶段 7：生成种子

基于 libtorrent 生成 BT 种子，内建 42 个动漫 tracker。

```python
targets = [p for p in (mp4_chs, mp4_cht, mkv_hevc) if p.exists()]

if targets:
    creator = TorrentCreator()
    for video in targets:
        creator.create(video, v1_only=True)  # anibt.net（动漫花园）需要 v1 only
```

**关键参数说明：**

**`TorrentCreator` 构造函数：**


| 参数             | 类型               | 默认值                            | 说明                                              |
| ---------------- | ------------------ | --------------------------------- | ------------------------------------------------- |
| `trackers`       | `list[str] | None` | `None`（使用 `DEFAULT_TRACKERS`） | 自定义 tracker 列表                               |
| `extra_trackers` | `list[str] | None` | `None`                            | 追加的额外 tracker（自动去重）                    |
| `piece_length`   | `int | None`       | `None`（自动计算）                | 分块大小（字节），自动选择使块数在 1000-2000 之间 |
| `comment`        | `str`              | `""`                              | 种子注释                                          |
| `created_by`     | `str`              | `"BML"`                           | 创建者标识                                        |

**`create(src, dst=None, v1_only=False)` → `Path`：**

- `src`：源文件/目录路径。
- `dst`：输出 `.torrent` 路径。`None` = 自动放在 `src` 同目录下，文件名 `{src.name}.torrent`。
- `v1_only`：`True` = 仅生成 v1 种子（兼容动漫花园/anibt.net）；`False` = v1+v2 hybrid。
- 内部流程：libtorrent 文件存储 → 自动分块大小计算 → 添加 tracker（每个独立 tier）→ 计算 piece hashes → 写入 bencode。

**分块大小自动计算规则：**


| 数据量   | 分块大小 |
| -------- | -------- |
| < 64 MB  | 64 KB    |
| < 512 MB | 256 KB   |
| < 2 GB   | 1 MB     |
| < 8 GB   | 4 MB     |
| ≥ 8 GB  | 8 MB     |

**`DEFAULT_TRACKERS`**：内建 42 个动漫 tracker，包括 nyaa、bangumi、acgtracker、openbittorrent 等。

### 阶段 7b：文件夹种子

```python
FOLDER = EP_DIR
folder = Path(FOLDER)
torrent_path = folder.parent / f"{folder.name}.torrent"
if not torrent_path.exists():
    TorrentCreator().create(folder, v1_only=True)
```

### 阶段 9：R2 上传

通过 Cloudflare R2 S3 兼容 API 上传文件，大文件（≥50MB）自动分片并发上传。

```python
targets = []
for p in (mp4_chs, mp4_cht, mkv_hevc):
    if p.exists():
        targets.append(p)
        t = p.with_suffix(p.suffix + ".torrent")
        if t.exists():
            targets.append(t)

if targets:
    uploader = R2Uploader()  # 凭证: ~/.config/bml/r2_config.json
    uploader.upload_files([str(t) for t in targets],
                          remote_folder=f"{R2_FOLDER}/{EP_ID}")
```

**关键参数说明：**

**`R2Uploader` 构造函数：**


| 参数                | 类型         | 默认值               | 说明                                                                |
| ------------------- | ------------ | -------------------- | ------------------------------------------------------------------- |
| `account_id`        | `str | None` | `None`（从配置读取） | Cloudflare Account ID                                               |
| `access_key_id`     | `str | None` | `None`（从配置读取） | R2 API Token Access Key ID                                          |
| `secret_access_key` | `str | None` | `None`（从配置读取） | R2 API Token Secret Access Key                                      |
| `bucket_name`       | `str | None` | `None`（从配置读取） | R2 存储桶名称                                                       |
| `endpoint`          | `str | None` | `None`（自动拼接）   | 自定义 S3 端点，默认`https://{account_id}.r2.cloudflarestorage.com` |

凭证优先级：**构造函数参数 > 环境变量 > `~/.config/bml/r2_config.json`**

**`upload_file(local_path, remote_key=None, progress=True)` → `str`：**

- `local_path`：本地文件路径。
- `remote_key`：R2 对象 key，`None` = 使用文件名。
- `progress`：是否打印进度。
- < 50MB 单次 PUT；≥ 50MB 分片上传（每片 50MB，最多 3 片并发，3 次重试）。

**`upload_files(paths, remote_folder="", progress=True)` → `list[str]`：**

- 批量上传，`remote_folder` 为 R2 目标文件夹（如 `"番剧名/01/"`）。单个文件失败不会中断其他文件。

**`sync_to_server(ssh_alias, remote_dir, r2_prefix="", delete_after=True)` → `bool`：**

- 流程：`rclone sync` → 逐文件 SHA-256 校验 → 校验通过后删除 R2 文件。
- `ssh_alias`：`~/.ssh/config` 中的别名。
- `remote_dir`：服务器目标目录。
- `r2_prefix`：R2 上要同步的前缀。
- `delete_after`：校验通过后是否删除 R2 文件。**安全机制**：如果没有本地哈希记录且 `delete_after=True`，拒绝删除。

**`list_remote(prefix="")` → `list[str]`**：列出 R2 文件 key 列表。

**`delete_remote(key)` → `bool`**：删除单个 R2 文件。

### 阶段 10：服务器 rclone 拉取

```python
uploader = R2Uploader()
r2_folder = f"{R2_FOLDER}/{EP_ID}"

if uploader.list_remote(r2_folder):
    uploader.sync_to_server(
        ssh_alias=SSH_ALIAS,
        remote_dir=REMOTE_DIR,
        r2_prefix=r2_folder,
    )
    # 自动: rclone sync → SHA-256 校验 → 删除 R2 文件
```

> 服务器需预先配置 rclone R2 remote（`rclone config`，创建名为 `r2` 的 S3 兼容 remote）。

### 阶段 11：远程做种

通过 SSH + qBittorrent Web API 将服务器上的种子添加做种。

```python
result = subprocess.run(
    ["ssh", SSH_ALIAS, f"ls '{REMOTE_DIR}'/*.torrent 2>/dev/null || echo ''"],
    capture_output=True, text=True, timeout=15,
)
torrent_files = [f for f in result.stdout.strip().split("\n") if f]

if torrent_files:
    seeder = RemoteSeeder(ssh_alias=SSH_ALIAS)
    seeder.add_torrents(torrent_files, save_path=REMOTE_DIR,
                        skip_checking=True, paused=False)
```

**关键参数说明：**

**`RemoteSeeder` 构造函数：**


| 参数            | 类型         | 默认值                          | 说明                                                   |
| --------------- | ------------ | ------------------------------- | ------------------------------------------------------ |
| `ssh_alias`     | `str`        | （必需）                        | `~/.ssh/config` 中配置的别名                           |
| `host`          | `str | None` | `None`（从配置读取）            | qB Web UI 主机名（服务器上可访问的地址），支持完整 URL |
| `port`          | `int | None` | `None`（从配置读取，默认 8081） | qB Web UI 端口                                         |
| `username`      | `str | None` | `None`（从配置读取）            | qB 登录用户名                                          |
| `password`      | `str | None` | `None`（从配置读取）            | qB 登录密码                                            |
| `download_base` | `str | None` | `None`（从配置读取）            | 服务器上视频文件下载根目录                             |

凭证优先级：**构造函数参数 > 环境变量 > `~/.config/bml/qb_config.json`**

**`login()` → `bool`**：登录 qBittorrent Web API，服务器端保存会话 cookie。

**`logout()` → `bool`**：登出并清理服务器 cookie。

**`add_torrent(remote_torrent_path, save_path=None, skip_checking=True, paused=False)` → `bool`：**

- `remote_torrent_path`：服务器上 `.torrent` 文件绝对路径。
- `save_path`：视频文件保存路径，默认 `self.download_base`。
- `skip_checking`：`True` = 跳过哈希校验（文件已在原位）。
- `paused`：`False` = 立即开始做种；`True` = 暂停状态添加。

**`add_torrents(remote_dir_or_paths, save_path=None, skip_checking=True, paused=False, glob_pattern="*.torrent")` → `dict[str, bool]`：**

- `remote_dir_or_paths`：服务器上目录（自动 find `.torrent`）或文件路径列表。
- `glob_pattern`：查找 `.torrent` 文件的 glob 模式。
- 返回 `{basename: True/False}`，`True` = 添加成功。

**`upload_and_seed(torrent_paths, remote_dir=None, save_path=None, skip_checking=True, paused=False)` → `dict[str, bool]`：**

- 先 SCP 上传本地 `.torrent` 到服务器，再添加做种。

**上下文管理器支持：**

```python
with RemoteSeeder(ssh_alias="my-server") as seeder:
    seeder.add_torrents("/path/to/downloads/")
# 自动 login / logout
```

### 阶段 12：API 发布

通过 anibt.net API 发布种子，支持 JSON+magnet 和直接上传 `.torrent` 两种方式。

```python
from bmlsub import Publisher

mp4_chs_t  = product_torrent_path(mp4_chs)
mp4_cht_t  = product_torrent_path(mp4_cht)
mkv_hevc_t = product_torrent_path(mkv_hevc)

FORMAT_META = {
    mp4_chs_t:  ("1080p", ["CHS", "JP"], "MP4", "EMBEDDED"),
    mp4_cht_t:  ("1080p", ["CHT", "JP"], "MP4", "EMBEDDED"),
    mkv_hevc_t: ("1080p", ["CHS", "CHT", "JP"], "MKV", "INTERNAL"),
}

for t in [t for t in (mp4_chs_t, mp4_cht_t, mkv_hevc_t) if t.exists()]:
    resolution, languages, fmt, subtitle = FORMAT_META[t]
    Publisher.publish_anibt(
        bgm_id=BGM_ID, title=t.stem, episode_key=EP_ID,
        torrent_path=t, resolution=resolution, languages=languages,
        subtitle=subtitle, fmt=fmt, notes="...", use_torrent_file=True,
    )
```

**关键参数说明：**

**`Publisher.publish_anibt()` 完整参数列表：**


| 参数               | 类型                | 默认值                        | 说明                                                                               |
| ------------------ | ------------------- | ----------------------------- | ---------------------------------------------------------------------------------- |
| `bgm_id`           | `int`               | （必需）                      | Bangumi 条目 ID                                                                    |
| `title`            | `str`               | （必需）                      | 发布标题（含组名、番名、集数等完整信息）                                           |
| `episode_key`      | `str`               | （必需）                      | 集数标识，如`"11"`                                                                 |
| `torrent_path`     | `str | Path | None` | `None`                        | 本地`.torrent` 文件路径                                                            |
| `magnet_base64`    | `str | None`        | `None`                        | Base64 编码的 magnet URI（方式一）                                                 |
| `resolution`       | `str`               | `"1080p"`                     | 分辨率                                                                             |
| `languages`        | `list[str] | None`  | `None`（→`[]`）              | 语言标签列表，如`["CHS", "CHT", "JP"]`                                             |
| `subtitle`         | `str`               | `"INTERNAL"`                  | 字幕类型：`"INTERNAL"` / `"EMBEDDED"`                                              |
| `fmt`              | `str`               | `"MKV"`                       | 格式：`"MKV"` / `"MP4"`                                                            |
| `file_size`        | `int | None`        | `None`（自动从 torrent 读取） | 文件大小（字节）                                                                   |
| `notes`            | `str`               | `""`                          | 发布说明（Markdown）                                                               |
| `trackers`         | `list[str] | None`  | `None`（自动从 torrent 读取） | Tracker 列表                                                                       |
| `token`            | `str | None`        | `None`（从配置读取）          | API Token                                                                          |
| `api_url`          | `str | None`        | `None`（从配置读取）          | API 地址                                                                           |
| `use_torrent_file` | `bool`              | `False`                       | `True` = 直接上传 `.torrent` 文件（multipart）；`False` = 提取 magnet 以 JSON 提交 |

凭证优先级：**函数参数 > 环境变量 `ANIBT_TOKEN`/`ANIBT_API_URL` > `~/.config/bml/anibt_config.json`**

**`Publisher.seed_qbittorrent(host, files, torrent_base_dir=None, download_base="/downloads", username="admin", password="")` → `dict[str, bool]`：**

- 直接连接 qBittorrent Web API 添加做种（使用 `qbittorrentapi` 库）。
- `host`：`"ip:port"` 格式。

### 流程总览


| 阶段 | 说明                   | 前置条件                                      |
| ---- | ---------------------- | --------------------------------------------- |
| 1    | 提取音轨 + 字幕        | `{id}.mkv`                                    |
| 2    | AI 转录                | 音轨 AAC                                      |
| 3    | HEVC VideoToolbox 编码 | `{id}.mkv`                                    |
| 4    | 字幕校验 & 标准化      | `.ass` 文件                                   |
| 5    | x264 硬压 + ASS 烧录   | `{id}.mkv` + `{id}.chs&jpn.ass`               |
| 6    | mkvmerge 封装          | `{id}_HEVC10bit.mkv` + 字幕 + 字体            |
| 7    | 生成 .torrent          | 阶段 5/6 产物                                 |
| 7b   | 文件夹种子             | 任意目录                                      |
| 9    | R2 上传                | 阶段 5/6 产物 +`~/.config/bml/r2_config.json` |
| 10   | R2 → 服务器 rclone    | 阶段 9 + rclone 配置                          |
| 11   | 远程 qBittorrent 做种  | 阶段 10 +`~/.config/bml/qb_config.json`       |
| 12   | anibt.net API 发布     | 阶段 7 +`~/.config/bml/anibt_config.json`     |

---

## 模块概览

```
bmlsub/
├── config.py      # PipelineConfig、EncodePreset、SubtitleStandard、预设常量
├── media.py       # MediaExtractor — 音轨/字幕提取（含智能筛选）
├── model_utils.py # 平台检测 & 模型管理 — 自动推荐/检查/下载转录模型
├── transcribe.py  # Transcriber — MLX Whisper 语音转文字（两种方法）
├── encode.py      # Encoder — HEVC 硬压 + x264 软编码 + 元数据清理
├── subtitle.py    # SubtitleValidator — ASS 头部校验与标准化
├── package.py     # Packager — mkvmerge 封装 + ffmpeg 硬压（自动匹配字幕）
├── torrent.py     # TorrentCreator — libtorrent 种子生成（42 个动漫 tracker）
├── transfer.py    # Transfer — croc P2P 加密传输 + SHA-256 双重校验
├── r2upload.py    # R2Uploader — Cloudflare R2 分片上传 + rclone 同步
├── seeder.py      # RemoteSeeder — SSH + qBittorrent Web API 远程做种
├── publish.py     # Publisher — anibt.net API 发布 + qBittorrent 做种
├── pipeline.py    # Pipeline — 高层流水线编排（一键全流程）
├── scan.py        # product_path / check_products / scan_products 产物检测
├── progress.py    # ProgressBar、SpeedMeter、PipelineTimer — 进度与计时
└── _backup.py     # backup_if_exists — 覆盖前自动备份
```

每个模块均可独立使用，不强依赖 Pipeline。

---

## API 参考

### config.py — 配置 & 预设

```python
from bmlsub import (
    PipelineConfig, EncodePreset, SubtitleStandard,
    PRESET_HEVC_VT_DEFAULT, PRESET_X264_SLOW,
    PRESET_X264_VERYSLOW, SUB_STANDARD_HD,
)
```

#### EncodePreset — 编码预设

`EncodePreset` 封装了 ffmpeg 视频 + 音频编码参数，通过 `to_ffmpeg_video_params()` 和 `to_ffmpeg_audio_params()` 生成 ffmpeg 命令行参数。


| 参数            | 类型         | 默认值                | 说明                                                                  |
| --------------- | ------------ | --------------------- | --------------------------------------------------------------------- |
| `codec`         | `str`        | `"hevc_videotoolbox"` | 视频编码器：`"hevc_videotoolbox"`（Mac 硬压）或 `"libx264"`（软编码） |
| `preset`        | `str`        | `"slow"`              | x264 preset：`"medium"`、`"slow"`、`"veryslow"` 等（仅 libx264 有效） |
| `crf`           | `int | None` | `None`                | 恒定质量参数。`None` → VideoToolbox 用 `-q:v`；libx264 默认 22       |
| `quality`       | `int`        | `60`                  | VideoToolbox 质量参数 (0-100)，对应 ffmpeg`-q:v`                      |
| `pixel_fmt`     | `str`        | `"p010le"`            | 像素格式：`"p010le"`（VT 10bit）或 `"yuv420p"`（x264 8bit）           |
| `audio_codec`   | `str`        | `"aac"`               | 音频编码器                                                            |
| `audio_bitrate` | `str`        | `"192k"`              | 音频码率                                                              |
| `extra_params`  | `list[str]`  | `[]`                  | 额外 ffmpeg 参数（如`-tune film`、`-x264-params ...`）                |

**方法：**

- `to_ffmpeg_video_params() → list[str]`：生成视频编码参数列表。
- `to_ffmpeg_audio_params() → list[str]`：生成音频编码参数列表。

**内置预设常量：**


| 常量                     | codec               | preset     | crf/q    | pixel_fmt | 说明                               |
| ------------------------ | ------------------- | ---------- | -------- | --------- | ---------------------------------- |
| `PRESET_HEVC_VT_DEFAULT` | `hevc_videotoolbox` | —         | `q:v 60` | `p010le`  | Mac HEVC 10bit 硬压默认            |
| `PRESET_X264_SLOW`       | `libx264`           | `slow`     | `22`     | `yuv420p` | x264 slow + tune film + 高质量参数 |
| `PRESET_X264_VERYSLOW`   | `libx264`           | `veryslow` | `22`     | `yuv420p` | x264 veryslow + tune film          |

#### SubtitleStandard — 字幕规范


| 参数                       | 类型  | 默认值     | 说明                                    |
| -------------------------- | ----- | ---------- | --------------------------------------- |
| `play_res_x`               | `int` | `1920`     | 播放分辨率宽度（ASS`PlayResX`）         |
| `play_res_y`               | `int` | `1080`     | 播放分辨率高度（ASS`PlayResY`）         |
| `color_matrix`             | `str` | `"TV.709"` | 色彩矩阵（ASS`YCbCr Matrix`）           |
| `script_type`              | `str` | `"v4.00+"` | ASS 脚本版本（`ScriptType`）            |
| `wrap_style`               | `int` | `0`        | 换行风格（`WrapStyle`）                 |
| `scaled_border_and_shadow` | `str` | `"yes"`    | 边框阴影缩放（`ScaledBorderAndShadow`） |

属性 `expected_header` 返回标准 ASS 头部的 `dict[str, str]`，供 `SubtitleValidator` 使用。

**内置预设：**`SUB_STANDARD_HD = SubtitleStandard()`（即所有默认值）。

#### PipelineConfig — 流水线总配置

`PipelineConfig` 用于 `Pipeline` 高层编排，集中管理所有模块的配置。


| 参数                     | 类型               | 默认值                                                      | 说明                                       |
| ------------------------ | ------------------ | ----------------------------------------------------------- | ------------------------------------------ |
| `work_dir`               | `Path`             | `Path(".").resolve()`                                       | 工作根目录                                 |
| `whisper_fast_model`     | `str`              | `"mlx-community/whisper-large-v3-turbo"`                    | 直接转录模型                               |
| `whisper_detailed_model` | `str`              | `"mlx-community/whisper-medium-mlx"`                        | 分割转录模型                               |
| `language`               | `str`              | `"ja"`                                                      | 音频语言                                   |
| `hevc_preset`            | `EncodePreset`     | `EncodePreset()`                                            | HEVC 编码预设                              |
| `x264_preset`            | `EncodePreset`     | `EncodePreset(codec="libx264", preset="slow", crf=22, ...)` | x264 编码预设（含 tune film 等高质量参数） |
| `sub_standard`           | `SubtitleStandard` | `SubtitleStandard()`                                        | 字幕校验规范                               |
| `chunk_sec`              | `int`              | `240`                                                       | 分割转录切片长度（秒）                     |
| `overlap_sec`            | `int`              | `5`                                                         | 切片重叠（秒）                             |
| `output_transcripts_dir` | `Path`             | `Path("./output_transcripts")`                              | 转录输出根目录                             |

---

### scan.py — 产物命名 & 检测

```python
from bmlsub import product_path, product_torrent_path, check_products, scan_products, PRODUCT_FORMATS
```

**`PRODUCT_FORMATS`** — 三个标准产物文件名模板（`{prefix}` `{ep_id}` 由调用方填入）：

```python
{
    "mp4_chs":  "{prefix} [{ep_id}][1080P][简日内嵌].mp4",
    "mp4_cht":  "{prefix} [{ep_id}][1080P][繁日內嵌].mp4",
    "mkv_hevc": "{prefix} [{ep_id}][1080P][HEVC-10bit][简繁日内封].mkv",
}
```

**`product_path(ep_dir, ep_id, product_key, prefix_chs, prefix_cht=None)` → `Path`：**

- `ep_dir`：集数目录路径。
- `ep_id`：集数编号，如 `"01"`。
- `product_key`：`"mp4_chs"` / `"mp4_cht"` / `"mkv_hevc"`。
- `prefix_chs`：简体中文前缀，如 `"[Billion Meta Lab] 作品名"`。
- `prefix_cht`：繁体中文前缀，默认同 `prefix_chs`。
- 仅构造路径，不检查存在性。`mp4_cht` key 会自动使用繁体前缀。

**`product_torrent_path(video_path)` → `Path | None`：**

- 视频路径 → 对应 `.torrent` 路径（`{video}.torrent`）。`None` 入参返回 `None`。纯构造，不检查存在性。

**`check_products(ep_dir, ep_id, prefix_chs, prefix_cht=None)` → `dict`：**

- 检查产物文件是否存在。返回：
  ```python
  {"mp4_chs": Path | None, "mp4_cht": Path | None,
   "mkv_hevc": Path | None, "all": list[Path]}
  ```

**`scan_products(ep_dir, ep_id, prefix_chs, prefix_cht=None)` → `dict`：**

- 完整扫描（含种子状态）。比 `check_products` 多出：
  ```python
  {"mp4_chs_torrent": Path | None, "mp4_cht_torrent": Path | None,
   "mkv_hevc_torrent": Path | None, "all_torrents": list[Path]}
  ```

---

### media.py — 素材提取

```python
from bmlsub import MediaExtractor, ExtractedTrack, PreferredSubs, SubtitleInfo
```

#### MediaExtractor

**构造函数：`MediaExtractor(work_dir=".")`**

- `work_dir`：工作目录，提取产物输出到此目录。

**方法一览：**


| 方法                                             | 说明                                    | 返回类型                         |
| ------------------------------------------------ | --------------------------------------- | -------------------------------- |
| `find_digit_mkvs()`                              | 找到纯数字命名（如`01.mkv`）的 MKV 文件 | `list[Path]`                     |
| `find_all_mkvs()`                                | 找到所有`.mkv` 文件                     | `list[Path]`                     |
| `probe_streams(video)`                           | ffprobe 获取所有流的 JSON 信息          | `list[dict]`                     |
| `list_subtitle_streams(video)`                   | 列出字幕流元信息（不提取）              | `list[SubtitleInfo]`             |
| `extract_audio_tracks(video, progress=None)`     | 提取所有音轨 → AAC 192k                | `list[ExtractedTrack]`           |
| `extract_subtitle_tracks(video)`                 | 提取所有字幕轨 → ASS                   | `list[ExtractedTrack]`           |
| `extract_preferred_subtitles(video, langs=None)` | 智能筛选（中/英/日优先）                | `PreferredSubs | None`           |
| `extract_all(video)`                             | 一键全量提取                            | `(音频列表, 字幕列表)`           |
| `extract_smart(video)`                           | 音轨全量 + 字幕智能筛选                 | `(音频列表, PreferredSubs|None)` |
| `get_audio_track(video, index=0)`                | 快速获取第 N 条音轨                     | `Path | None`                    |

**`extract_preferred_subtitles(video, langs=None)` 详细说明：**

- `langs`：语言优先级列表，默认 `["chi", "eng", "jpn"]`。
- 排序逻辑：先分类（`chi` / `eng` / `jpn` / `other`），若只有一种语言类别则全量提取，否则按优先级提取。
- 返回 `PreferredSubs`（含 `.chi` `.eng` `.jpn` `.other` 四个列表）。无字幕时返回 `None`。

**类方法（语言判定）：**

- `MediaExtractor.is_chi(lang)` → `bool`
- `MediaExtractor.is_eng(lang)` → `bool`
- `MediaExtractor.is_jpn(lang)` → `bool`

识别语言代码范围：

- 中文：`chi`, `zh`, `zho`, `chs`, `cht`, `zh-cn`, `zh-tw`, `zh-hans`, `zh-hant`
- 英文：`eng`, `en`, `en-us`, `en-gb`
- 日文：`jpn`, `ja`, `jp`

#### ExtractedTrack — 提取轨道信息

```python
@dataclass
class ExtractedTrack:
    index: int          # 原始流索引
    codec_type: str     # 'audio' | 'subtitle'
    language: str       # 语言代码: 'jpn', 'eng', 'chi'...
    title: str          # 轨道标题
    codec_name: str     # 编解码器名
    output_path: Path   # 提取后的文件路径
```

#### PreferredSubs — 智能筛选结果

```python
@dataclass
class PreferredSubs:
    chi: list[ExtractedTrack]    # 中文
    eng: list[ExtractedTrack]    # 英文
    jpn: list[ExtractedTrack]    # 日文
    other: list[ExtractedTrack]  # 其他
```


| 属性/方法       | 说明                                     |
| --------------- | ---------------------------------------- |
| `.total_count`  | 总轨道数                                 |
| `.has_any`      | 是否有任何字幕                           |
| `.all_tracks()` | 返回`chi + eng + jpn + other` 拼接列表   |
| `.summary()`    | 返回中文摘要，如`"中文 2 条; 英文 1 条"` |

#### SubtitleInfo — 字幕流元信息

```python
@dataclass
class SubtitleInfo:
    index: int          # 流索引
    language: str       # 语言代码
    title: str          # 轨道标题（如 'Simplified', 'English'）
    codec_name: str     # 编码（如 'subrip', 'ass'）
```

---

### model_utils.py — 平台检测 & 模型管理

```python
from bmlsub import (
    detect_platform, is_apple_silicon, get_recommended_models,
    check_model_available, download_model, resolve_model,
    list_cached_models, print_model_guide,
    ModelRecommendation, ResolvedModel,
)
```

> **macOS 专用说明：** MLX Whisper 仅支持 macOS（Apple Silicon 原生加速，Intel 通过 Rosetta 兼容）。非 macOS 平台自动推荐 faster-whisper（CTranslate2）。

#### detect_platform()

```python
>>> detect_platform()
{"system": "Darwin", "machine": "arm64", "is_macos": True, "is_apple_silicon": True, "python_version": "3.12..."}
```

返回当前平台信息字典。

#### is_apple_silicon()

快捷方法 → `bool`。等价于 `platform.system() == "Darwin" and platform.machine() == "arm64"`。

#### get_recommended_models(language="ja")

根据当前平台和语言返回推荐模型列表（`list[ModelRecommendation]`，按优先级排序，第 0 个为首选）。

**推荐逻辑：**


| 平台                | 语言 | 首选模型                            | 后端           |
| ------------------- | ---- | ----------------------------------- | -------------- |
| macOS Apple Silicon | 日语 | `kotoba-whisper-v2.0-8bit`          | MLX            |
| macOS Apple Silicon | 通用 | `whisper-large-v3-turbo`            | MLX            |
| 其他平台            | 日语 | `faster-whisper-large-v3-turbo-ct2` | faster-whisper |
| 其他平台            | 通用 | `faster-whisper-large-v3-turbo-ct2` | faster-whisper |

**`ModelRecommendation` 数据类字段：**


| 字段             | 类型    | 说明                          |
| ---------------- | ------- | ----------------------------- |
| `model_id`       | `str`   | HF repo 路径                  |
| `backend`        | `str`   | `"mlx"` / `"faster_whisper"`  |
| `name`           | `str`   | 人类可读简称                  |
| `description`    | `str`   | 详细说明                      |
| `speed`          | `str`   | `"最快"` / `"快"` / `"中"`    |
| `accuracy`       | `str`   | `"最高"` / `"高"` / `"中"`    |
| `lang_specialty` | `str`   | `"日语专用"` / `"多语言通用"` |
| `size_gb`        | `float` | 约大小 (GB)                   |
| `install_cmd`    | `str`   | pip install 命令              |
| `cache_dir_help` | `str`   | 缓存目录说明                  |

#### check_model_available(model_id, backend="auto") → bool

检查模型是否已下载到本地缓存。

- `model_id`：HF repo 路径或本地路径。
- `backend`：`"mlx"` / `"faster_whisper"` / `"auto"`。`"auto"` 根据 model_id 前缀自动判断。
- 分别检查 HF 缓存目录（`.safetensors` / `model.bin`）和本地目录。

#### download_model(model_id, backend="auto", force=False) → bool

从 HuggingFace 下载模型到本地缓存。

- 使用 `huggingface_hub.snapshot_download()`。
- MLX 模型自动过滤不需要的 `pytorch_model*`、`tf_model*` 等文件。
- 依赖 `huggingface_hub`（`pip install huggingface_hub`）。

#### resolve_model(model_id=None, language="ja", backend=None, auto_download=False) → ResolvedModel

**这是阶段 2 模型选择的核心入口。** 自动完成平台检测 → 模型推荐 → 可用性检查 → 下载指引。

所有参数均为可选：`resolve_model()` 不带参数即可获得当前平台最佳推荐。


| 参数            | 类型         | 默认值  | 说明                                                         |
| --------------- | ------------ | ------- | ------------------------------------------------------------ |
| `model_id`      | `str | None` | `None`  | `None`=自动推荐；HF 路径=使用指定模型；本地路径=跳过下载检查 |
| `language`      | `str`        | `"ja"`  | 音频语言，影响日语专用模型的推荐                             |
| `backend`       | `str | None` | `None`  | 强制指定后端，`None`=根据平台自动选择                        |
| `auto_download` | `bool`       | `False` | `True`=不可用时自动下载                                      |

**`ResolvedModel` 数据类字段：**


| 字段             | 类型                         | 说明                                      |
| ---------------- | ---------------------------- | ----------------------------------------- |
| `model_id`       | `str`                        | 最终使用的模型 ID 或路径                  |
| `backend`        | `str`                        | `"mlx"` / `"faster_whisper"` / `"openai"` |
| `available`      | `bool`                       | 模型是否在本地可用                        |
| `cache_path`     | `str | None`                 | 本地缓存路径                              |
| `platform_info`  | `dict`                       | `detect_platform()` 返回值                |
| `recommendation` | `ModelRecommendation | None` | 匹配到的推荐（`None`=用户自定义）         |
| `notes`          | `list[str]`                  | 额外指引/警告信息                         |

**使用示例：**

```python
# 最简用法 — 自动选择最佳模型
info = resolve_model(language="ja")
print(info.model_id)    # "mlx-community/kotoba-whisper-v2.0-8bit" (Mac)
print(info.available)   # True / False
for note in info.notes: # 打印指引
    print(note)

# 指定模型
info = resolve_model("mlx-community/whisper-large-v3-turbo")

# 自定义本地路径
info = resolve_model("~/models/my-whisper")

# 自动下载
info = resolve_model(auto_download=True)

# 配合 Transcriber 使用
from bmlsub import Transcriber
t = Transcriber(model=info.model_id, language="ja")
t.transcribe_direct(audio_path)
```

#### list_cached_models() → list[str]

列出本地已缓存的转录模型（扫描 HF 缓存目录）。

#### print_model_guide(language="ja")

打印完整的模型选择指引，包括：

- 当前平台信息
- 已缓存模型列表
- 推荐模型详细信息（名称、后端、速度、精度、大小、安装命令）
- 快速上手代码示例
- 自定义路径说明

```python
>>> print_model_guide(language="ja")
============================================================
📋 转录模型选择指引
============================================================
🖥️  当前平台:
   系统: Darwin (arm64)
   Apple Silicon: ✅ 是
   推荐后端: MLX Whisper

📦 已缓存模型 (2 个):
   ✅ mlx-community/whisper-large-v3-turbo
   ✅ mlx-community/kotoba-whisper-v2.0-8bit

🎯 推荐模型 (语言: ja):
  🥇 首选: kotoba-whisper-v2.0 (8bit)
         模型: mlx-community/kotoba-whisper-v2.0-8bit
         后端: mlx
         速度: 快  |  精度: 最高  |  语言: 日语专用
         大小: ~1.5 GB
         安装: pip install mlx-whisper
   ...
============================================================
```

---

### transcribe.py — AI 转录

```python
from bmlsub import Transcriber, TranscriptionError, model_short_name
```

#### Transcriber

**构造函数：`Transcriber(model, language, chunk_sec=240, overlap_sec=5, export_format="mp3", output_root="./output_transcripts")`**


| 参数            | 类型         | 默认值                                   | 说明                   |
| --------------- | ------------ | ---------------------------------------- | ---------------------- |
| `model`         | `str`        | `"mlx-community/whisper-large-v3-turbo"` | 默认模型 HF 路径       |
| `language`      | `str`        | `"ja"`                                   | 音频语言代码           |
| `chunk_sec`     | `int`        | `240`                                    | 分割转录切片长度（秒） |
| `overlap_sec`   | `int`        | `5`                                      | 相邻切片重叠秒数       |
| `export_format` | `str`        | `"mp3"`                                  | 切片导出音频格式       |
| `output_root`   | `str | Path` | `"./output_transcripts"`                 | 转录输出根目录         |

**`transcribe_direct(audio_path, model=None, output_path=None, force=False)` → `Path | None`：**

- 方法 1 — 直接转录：整轨一次 `mlx_whisper.transcribe()`，速度快。
- 输出格式：`{stem}_direct_{模型简称}.txt`。
- 已存在文件默认跳过（`force=False`）。
- 返回输出文件路径；异常抛出 `TranscriptionError`。

**`transcribe_chunked(audio_path, model=None, manual_cuts=None, output_dir=None, force=False)` → `Path | None`：**

- 方法 2 — 分割转录：音频分段 → 滑窗切片 → 逐片转录 → 合并。
- 输出格式：`{stem}_chunked_{模型简称}_final.txt`。
- `manual_cuts`：手动切点 `["MM:SS", ...]`，格式支持 `"MM:SS"` 或 `"HH:MM:SS"`。用于在 OP/ED 处切分，跳过歌曲段。
- `output_dir`：工作目录（含切片和中间转录文件），`None` 自动创建 `output_transcripts/work_{stem}_{模型简称}/`。
- 内部使用 `pydub.AudioSegment` 进行音频切分。

**`transcribe_both(audio_path, fast_model, detailed_model, manual_cuts=None)` → `dict`：**

- 依次执行两种转录：方法 1 用 `fast_model`，方法 2 用 `detailed_model`。
- 单个方法失败不会中断另一个。
- 返回 `{"direct": Path | None, "chunked": Path | None}`。

**推荐模型选择：**


| 模型                                     | 速度 | 精度 | 适用                       |
| ---------------------------------------- | ---- | ---- | -------------------------- |
| `mlx-community/whisper-large-v3-turbo`   | 快   | 高   | 直接转录首选               |
| `mlx-community/whisper-medium-mlx`       | 中   | 中   | 分割转录（平衡速度与精度） |
| `mlx-community/kotoba-whisper-v2.0-8bit` | 快   | 最高 | 日语专用                   |

#### model_short_name()

```python
model_short_name("mlx-community/whisper-large-v3-turbo")
# → "large-v3-turbo"
```

---

### encode.py — 视频编码

```python
from bmlsub import Encoder
```

#### Encoder

**构造函数：`Encoder(hevc_preset=None, x264_preset=None)`**

- `hevc_preset`：HEVC 编码预设，默认 `PRESET_HEVC_VT_DEFAULT`。
- `x264_preset`：x264 编码预设，默认 `PRESET_X264_SLOW`。

**`encode_hevc_vt(src, dst=None, audio_streams=None, strip_metadata=True)` → `Path`：**


| 参数             | 类型               | 默认值                                  | 说明               |
| ---------------- | ------------------ | --------------------------------------- | ------------------ |
| `src`            | `Path`             | （必需）                                | 源 MKV 文件        |
| `dst`            | `Path | None`      | `None`（→ `{src.stem}_HEVC10bit.mkv`） | 输出路径           |
| `audio_streams`  | `list[int] | None` | `None`（保留全部音轨）                  | 要保留的音轨流索引 |
| `strip_metadata` | `bool`             | `True`                                  | 编码后清理元数据   |

编码流程：

1. ffmpeg HEVC VideoToolbox 编码（`-c:v hevc_videotoolbox -allow_sw 1 -profile:v main10 -pix_fmt p010le`）
2. 覆盖前备份旧文件到 `_backup/`
3. 编码完成后 `mkvpropedit --delete-track-statistics-tags` + `--tags all:` 深度清理

**`encode_x264(src, dst, ass_subtitle=None, preset=None)` → `Path`：**


| 参数           | 类型                  | 默认值                            | 说明                                                |
| -------------- | --------------------- | --------------------------------- | --------------------------------------------------- |
| `src`          | `Path`                | （必需）                          | 源视频                                              |
| `dst`          | `Path`                | （必需）                          | 输出`.mp4` 路径                                     |
| `ass_subtitle` | `Path | None`         | `None`                            | ASS 字幕（通过`-vf ass=...` 烧录），`None` = 不烧录 |
| `preset`       | `EncodePreset | None` | `None`（使用 `self.x264_preset`） | 编码预设                                            |

**`strip_metadata(video_path)` → `Path`：**

- 单独对已编码文件执行元数据深度清理。
- 优先使用 `mkvpropedit`，不可用时回退到 `ffmpeg -c copy` 流拷贝。

**`verify_metadata_clean(video_path)` → `dict`：**

- 检查项包括 `_STATISTICS_*` 标签、`BPS`、`DURATION`、`NUMBER_OF_FRAMES`、`NUMBER_OF_BYTES`、`title`、`HANDLER_NAME`、`VENDOR_ID`。
- 返回 `{"stream_0_video": ["_STATISTICS_WRITING_APP"], ...}` 或空 `dict`（干净）。

---

### subtitle.py — 字幕校验

```python
from bmlsub import SubtitleValidator
```

#### SubtitleValidator

**构造函数：`SubtitleValidator(standard=None)`**

- `standard`：`SubtitleStandard` 实例，默认 `SUB_STANDARD_HD`（1920×1080, TV.709, v4.00+）。


| 方法                                                       | 说明                                      | 返回类型         |
| ---------------------------------------------------------- | ----------------------------------------- | ---------------- |
| `check_subtitle_exists(episode_dir, episode_id, sub_type)` | 检查`{ep_id}.{sub_type}&jpn.ass` 是否存在 | `Path | None`    |
| `validate_for_episode(episode_dir, episode_id)`            | 单集完整校验（chs + cht）                 | `dict`           |
| `validate_ass_header(ass_path)`                            | 检查 ASS 头部合规性                       | `dict[str, str]` |
| `standardize_ass(ass_path, output_path=None)`              | 修正 ASS 头部                             | `Path`           |
| `standardize_extracted_subs(episode_dir, episode_id)`      | 批量标准化原始提取字幕                    | `list[Path]`     |

**`validate_ass_header(ass_path)` 详细说明：**

- 解析 `[Script Info]` 段，对比 `SubtitleStandard.expected_header`。
- 返回不合规字段字典：`{"PlayResX": "1280", "YCbCr Matrix": "(缺失)"}`。
- 空 `dict` = 完全合规。

**`standardize_ass(ass_path, output_path=None)` 详细说明：**

- `output_path=None` 时覆盖原文件（先备份到 `_backup/`）。
- 只修改 `[Script Info]` 段，不触碰 `[V4+ Styles]` 和 `[Events]`。

**`validate_for_episode(episode_dir, episode_id)` 返回结构：**

```python
{
    "chs": {"exists": bool, "path": Path|None, "header_ok": bool, "issues": list},
    "cht": {"exists": bool, "path": Path|None, "header_ok": bool, "issues": list},
    "all_ok": bool,
}
```

---

### package.py — 封装

```python
from bmlsub import Packager, PackagingError
```

#### Packager

**构造函数：`Packager(episode_dir, episode_id, config=None)`**


| 参数          | 类型                    | 说明                                   |
| ------------- | ----------------------- | -------------------------------------- |
| `episode_dir` | `Path | str`            | 集数目录                               |
| `episode_id`  | `str`                   | 集数编号，如`"01"`                     |
| `config`      | `PipelineConfig | None` | 流水线配置，默认新建`PipelineConfig()` |

**`get_available_files()` → `dict`：**

- 自动扫描目录匹配所有字幕/视频/字体文件。
- 字幕匹配模式（按优先级）：`{stem}.chs&jpn.ass` → `{stem}.cht&jpn.ass` → `{stem}.chs.ass` → `{stem}.cht.ass` → `{stem}_sub_chi_*.ass` → `{stem}_sub_eng_*.ass` → `{stem}_sub_jpn_*.ass`
- 字体匹配：`*.ttf`、`*.otf`、`*.ttc`。
- 返回结构：
  ```python
  {
      "pure_mkv": Path | None,    # {ep_id}.mkv
      "hevc_mkv": Path | None,    # {ep_id}_HEVC10bit.mkv
      "chs_sub": Path | None,     # 简体中文字幕
      "cht_sub": Path | None,     # 繁体中文字幕
      "eng_sub": Path | None,     # 英文字幕
      "jpn_sub": Path | None,     # 日文字幕
      "all_subs": list[Path],     # 所有匹配到的字幕
      "fonts": list[Path],        # 所有字体文件
  }
  ```

**`mkvmerge_package(output_template)` → `Path | None`：**

- `output_template`：输出文件名模板，`"&&"` 会被替换为集数。
- 例：`"[BML] Series [&&][HEVC-10bit][CHS&CHT&JP].mkv"`。
- 自动检测字幕语言元数据（简中 → `default-track: yes`，繁中 → `default-track: no`）。
- 自动附加全部字体文件（TTF/OTF/TTC）。

**`ffmpeg_hardsub_encode(chs_template, cht_template)` → `list[Path]`：**

- 对纯数字 MKV 烧录简/繁 ASS 硬字幕，输出两个 MP4。
- `chs_template` / `cht_template`：输出文件名模板，`"&&"` 替换为集数。
- 使用 `PipelineConfig.x264_preset` 的编码参数。

**`package_all(mkv_tmpl, chs_tmpl, cht_tmpl)` → `list[Path]`：**

- 一键执行 mkvmerge + ffmpeg 硬压。
- 先校验字幕存在性，缺失的字幕对应步骤会被跳过。

---

### torrent.py — 种子生成

```python
from bmlsub import TorrentCreator, DEFAULT_TRACKERS
```

#### TorrentCreator

**构造函数：`TorrentCreator(trackers=None, extra_trackers=None, piece_length=None, comment="", created_by="BML")`**


| 参数             | 类型               | 默认值                          | 说明                                                   |
| ---------------- | ------------------ | ------------------------------- | ------------------------------------------------------ |
| `trackers`       | `list[str] | None` | `None`（→ `DEFAULT_TRACKERS`） | 自定义 tracker 列表，`None` 使用内建 42 个动漫 tracker |
| `extra_trackers` | `list[str] | None` | `None`                          | 额外追加的 tracker，自动去重                           |
| `piece_length`   | `int | None`       | `None`（自动计算）              | 分块大小（字节）                                       |
| `comment`        | `str`              | `""`                            | 种子注释                                               |
| `created_by`     | `str`              | `"BML"`                         | 创建者标识                                             |

**`create(src, dst=None, v1_only=False)` → `Path`：**


| 参数      | 类型                | 说明                                                   |
| --------- | ------------------- | ------------------------------------------------------ |
| `src`     | `Path | str`        | 源文件或目录                                           |
| `dst`     | `Path | str | None` | 输出`.torrent` 路径，`None` 自动放 `src` 同目录        |
| `v1_only` | `bool`              | `True` = 仅 v1（兼容动漫花园）；`False` = v1+v2 hybrid |

每个 tracker 分配独立 tier，确保 DHT/PEX 之外的 tracker 冗余。

**`DEFAULT_TRACKERS`** — 42 个动漫 tracker，涵盖：

- nyaa、bangumi、acgtracker、dmhy
- openbittorrent、publicbt、opentrackr
- 及各种动漫专用 tracker

**分块大小自动计算：**


| 源数据量        | 分块大小 | 约块数    |
| --------------- | -------- | --------- |
| < 64 MB         | 64 KB    | ≤ 1000   |
| 64 MB – 512 MB | 256 KB   | 250–2000 |
| 512 MB – 2 GB  | 1 MB     | 500–2000 |
| 2 GB – 8 GB    | 4 MB     | 500–2000 |
| ≥ 8 GB         | 8 MB     | ≥ 1000   |

---

### transfer.py — 安全传输

```python
from bmlsub import Transfer, TransferError, SSHConnectionError, HashVerificationError, CrocTransferError
```

#### Transfer

**构造函数：`Transfer(ssh_config=None, remote_dir="/opt/qb/downloads")`**

- `ssh_config`：`{"host": str, "port": int, "user": str, "key_path": str}`。
- `remote_dir`：远程目标目录。

**`send_files(local_paths, verify=True, cleanup=True)` → `bool`：**

完整传输流程，返回 `True` = 全部传输并校验通过。


| 参数          | 类型               | 默认值   | 说明                      |
| ------------- | ------------------ | -------- | ------------------------- |
| `local_paths` | `list[str | Path]` | （必需） | 本地文件/目录列表         |
| `verify`      | `bool`             | `True`   | 是否执行远程 SHA-256 校验 |
| `cleanup`     | `bool`             | `True`   | 是否清理两端临时 tar.gz   |

传输协议：

1. **本地**：计算 SHA-256 → tar.gz 打包
2. **croc**：加密 P2P 传输（通过 PTY 捕获随机暗号）
3. **SSH**：远程 `croc receive`（10 分钟超时保护）
4. **远程**：压缩包哈希校验 → 解压 → 逐文件哈希对账
5. **清理**：自动清理两端临时文件

**异常类：**

- `TransferError`：通用传输异常
- `SSHConnectionError`：SSH 连接失败
- `HashVerificationError`：哈希校验不一致
- `CrocTransferError`：croc 传输失败/暗号超时

---

### r2upload.py — Cloudflare R2 上传

```python
from bmlsub import R2Uploader, R2UploadError
```

#### R2Uploader

**类常量：**


| 常量                  | 值    | 说明             |
| --------------------- | ----- | ---------------- |
| `MULTIPART_THRESHOLD` | 50 MB | 大于此值自动分片 |
| `MULTIPART_CHUNKSIZE` | 50 MB | 每片大小         |
| `MAX_CONCURRENCY`     | 3     | 最大并发分片数   |
| `MAX_RETRIES`         | 3     | 失败重试次数     |

**构造函数：`R2Uploader(account_id=None, access_key_id=None, secret_access_key=None, bucket_name=None, endpoint=None)`**

所有参数均可 `None`，凭证从环境变量或配置文件读取。凭证优先级：**参数 > 环境变量 > `~/.config/bml/r2_config.json`**。

环境变量对应关系：`R2_ACCOUNT_ID`、`R2_ACCESS_KEY_ID`、`R2_SECRET_ACCESS_KEY`、`R2_BUCKET_NAME`、`R2_ENDPOINT`。

**`upload_file(local_path, remote_key=None, progress=True)` → `str`：**

- 上传单个文件。小文件（< 50MB）单次 PUT，大文件 boto3 分片上传（带进度回调）。
- 上传前计算 SHA-256 并记录到 `self._hashes`，供后续 `sync_to_server` 校验。

**`upload_files(paths, remote_folder="", progress=True)` → `list[str]`：**

- 批量上传，失败不中断。`remote_folder` 为 R2 目标路径前缀。

**`sync_to_server(ssh_alias, remote_dir, r2_prefix="", delete_after=True)` → `bool`：**

- 服务器端通过 `rclone sync` 从 R2 拉取 → 逐文件 SHA-256 校验 → 校验通过后删除 R2 文件。
- 安全机制：无本地哈希记录 + `delete_after=True` → 拒绝删除。

**`list_remote(prefix="")` → `list[str]`**：列出 R2 对象 key。

**`delete_remote(key)` → `bool`**：删除单个 R2 对象。

---

### seeder.py — 远程做种

```python
from bmlsub import RemoteSeeder, SeederError
```

#### RemoteSeeder

**构造函数：`RemoteSeeder(ssh_alias, host=None, port=None, username=None, password=None, download_base=None)`**


| 参数            | 类型         | 默认值                                    | 说明                                   |
| --------------- | ------------ | ----------------------------------------- | -------------------------------------- |
| `ssh_alias`     | `str`        | （必需）                                  | SSH 别名（`~/.ssh/config`）            |
| `host`          | `str | None` | `None`（→ `"localhost"`）                | qB Web UI 主机，支持`http://` 完整 URL |
| `port`          | `int | None` | `None`（→ `8081`）                       | qB Web UI 端口                         |
| `username`      | `str | None` | `None`（→ `"admin"`）                    | 登录用户名                             |
| `password`      | `str | None` | `None`（→ `""`）                         | 登录密码                               |
| `download_base` | `str | None` | `None`（→ `""`）                   | 服务器端下载根目录（须配置）            |

凭证优先级：**参数 > 环境变量（`QB_HOST`、`QB_PORT`、`QB_USERNAME`、`QB_PASSWORD`、`QB_DOWNLOAD_BASE`）> `~/.config/bml/qb_config.json`**

**方法一览：**


| 方法                                                                                | 说明                   | 返回类型          |
| ----------------------------------------------------------------------------------- | ---------------------- | ----------------- |
| `login()`                                                                           | 登录 qB Web API        | `bool`            |
| `logout()`                                                                          | 登出并清理 cookie      | `bool`            |
| `add_torrent(remote_torrent_path, save_path, skip_checking, paused)`                | 添加单个服务器上的种子 | `bool`            |
| `add_torrents(remote_dir_or_paths, save_path, skip_checking, paused, glob_pattern)` | 批量添加               | `dict[str, bool]` |
| `upload_and_seed(torrent_paths, remote_dir, save_path, skip_checking, paused)`      | SCP 上传 + 添加做种    | `dict[str, bool]` |

**`add_torrent()` 参数：**


| 参数                  | 类型         | 默认值                            | 说明                        |
| --------------------- | ------------ | --------------------------------- | --------------------------- |
| `remote_torrent_path` | `str | Path` | （必需）                          | 服务器上`.torrent` 绝对路径 |
| `save_path`           | `str | None` | `None`（→ `self.download_base`） | 视频文件保存路径            |
| `skip_checking`       | `bool`       | `True`                            | 跳过哈希校验                |
| `paused`              | `bool`       | `False`                           | `True` = 暂停状态添加       |

**`add_torrents()` 参数：**

- `remote_dir_or_paths`：目录（自动 `find *.torrent`）或文件路径列表。含 `.` 的文件名视为文件路径。
- `glob_pattern`：`"*.torrent"`，目录模式下查找种子文件的模式。

**`upload_and_seed()` 参数：**

- `torrent_paths`：本地 `.torrent` 文件路径列表。
- `remote_dir`：SCP 目标目录，默认 `self.download_base`。

**上下文管理器：**

```python
with RemoteSeeder(ssh_alias="my-server") as seeder:
    seeder.add_torrents(...)
# 自动 login/logout
```

---

### publish.py — API 发布

```python
from bmlsub import Publisher, PublishError
```

#### Publisher

**`Publisher.publish_anibt(bgm_id, title, episode_key, torrent_path=None, magnet_base64=None, *, resolution, languages, subtitle, fmt, file_size, notes, trackers, token, api_url, use_torrent_file)` → `dict`：**

完整参数列表见阶段 12 文档。

两种发布方式：

- **方式一（JSON + magnet）**：传入 `torrent_path`（自动提取 info_hash/magnet）或 `magnet_base64`，以 JSON body POST。
- **方式二（上传 .torrent 文件）**：传入 `torrent_path` + `use_torrent_file=True`，以 `multipart/form-data` POST。

**`Publisher.seed_qbittorrent(host, files, torrent_base_dir=None, download_base="/downloads", username="admin", password="")` → `dict[str, bool]`：**

- 使用 `qbittorrentapi` 库直接连接 qBittorrent Web API。
- `host`：`"ip:port"` 格式。
- `torrent_base_dir`：`.torrent` 文件目录，默认与视频同目录。

---

### pipeline.py — 流水线编排

```python
from bmlsub import Pipeline
```

#### Pipeline

**构造函数：`Pipeline(config=None, **kwargs)`**

- `config`：`PipelineConfig` 实例。`None` 时用 `**kwargs` 构造 `PipelineConfig`。
- 所有子模块（`MediaExtractor`、`Transcriber`、`Encoder`、`SubtitleValidator`）延迟初始化。

**主要方法：**


| 方法                                                                                    | 说明                               |
| --------------------------------------------------------------------------------------- | ---------------------------------- |
| `extract_media(episode_dir, episodes, smart_subs)`                                      | 阶段 1：提取音轨+字幕              |
| `transcribe_episode(episode_dir, episode_id, direct_model, chunked_model, manual_cuts)` | 阶段 2：AI 转录                    |
| `encode_episode(episode_dir, episode_id)`                                               | 阶段 3：HEVC 编码                  |
| `encode_hevc_batch(episode_dir, episodes)`                                              | 批量 HEVC 编码                     |
| `validate_subtitles(episode_dir, episode_id)`                                           | 阶段 4：字幕校验                   |
| `package_episode(episode_dir, episode_id, mkv_template, chs_template, cht_template)`    | 阶段 5：封装                       |
| `transfer_files(file_paths, ssh_config, remote_dir)`                                    | 阶段 6：croc+SSH 传输              |
| `seed_torrents(files, qb_host, qb_user, qb_pass, download_base)`                        | 阶段 7：qB 做种                    |
| `process_episode(episode_dir, episode_id, ...)`                                         | 一键全流程（支持 skip_* 跳过阶段） |

**`process_episode()` 完整参数：**


| 参数              | 类型          | 默认值                | 说明                                  |
| ----------------- | ------------- | --------------------- | ------------------------------------- |
| `episode_dir`     | `Path | str`  | （必需）              | 集数目录                              |
| `episode_id`      | `str | None`  | `None`（自动推断）    | 集数编号                              |
| `manual_cuts`     | `dict | None` | `None`                | `{"01": ["10:00", "20:00"]}` 手动切点 |
| `direct_model`    | `str | None`  | `None`                | 直接转录模型                          |
| `chunked_model`   | `str | None`  | `None`                | 分割转录模型                          |
| `mkv_template`    | `str | None`  | `None`                | MKV 输出模板                          |
| `chs_template`    | `str | None`  | `None`                | CHS MP4 模板                          |
| `cht_template`    | `str | None`  | `None`                | CHT MP4 模板                          |
| `ssh_config`      | `dict | None` | `None`                | SSH 配置                              |
| `remote_dir`      | `str`         | `"/opt/qb/downloads"` | 远程目录                              |
| `qb_host`         | `str | None`  | `None`                | qB 主机                               |
| `skip_transcribe` | `bool`        | `False`               | 跳过转录                              |
| `skip_encode`     | `bool`        | `False`               | 跳过编码                              |
| `skip_package`    | `bool`        | `False`               | 跳过封装                              |
| `skip_transfer`   | `bool`        | `False`               | 跳过传输                              |
| `skip_seed`       | `bool`        | `False`               | 跳过做种                              |

---

### progress.py — 进度 & 计时

```python
from bmlsub import ProgressBar, SpeedMeter, StageTimer, PipelineTimer
```

#### SpeedMeter — 滑动窗口网速计

**构造函数：`SpeedMeter(window_sec=5.0)`**

- `window_sec`：速度计算的滑动窗口时长（秒）。


| 属性             | 类型    | 说明                                      |
| ---------------- | ------- | ----------------------------------------- |
| `.speed`         | `float` | 当前窗口内速度 (bytes/sec)                |
| `.avg_speed`     | `float` | 全程平均速度 (bytes/sec)                  |
| `.speed_str`     | `str`   | 当前速度的人类可读字符串，如`"12.5 MB/s"` |
| `.avg_speed_str` | `str`   | 平均速度的人类可读字符串                  |
| `.total_str`     | `str`   | 已传输总量的人类可读字符串                |
| `.elapsed`       | `float` | 已耗时（秒）                              |
| `.elapsed_str`   | `str`   | 已耗时的人类可读字符串                    |


| 方法           | 说明           |
| -------------- | -------------- |
| `add_bytes(n)` | 记录新增字节数 |
| `reset()`      | 重置计数器     |

#### ProgressBar — 通用进度条

封装 tqdm，自动显示百分比、速度、ETA。

**构造函数：`ProgressBar(label, total, unit="B", show_speed=True, show_eta=True, bar_format=None)`**

- `label`：进度条标签。
- `total`：总量（字节数/帧数/百分比）。
- `unit`：单位，`"B"` 自动换算。
- `show_speed`：显示网速。
- `show_eta`：显示预计剩余时间。

**工厂方法：**

- `ProgressBar.file_upload(filename, total_bytes)` — 文件上传进度条
- `ProgressBar.file_download(filename, total_bytes)` — 文件下载进度条
- `ProgressBar.encode(filename, duration_sec)` — 编码进度条

**上下文管理器支持：**

```python
with ProgressBar("Encoding", total=100) as bar:
    for i in range(100):
        bar.update(1)
```

#### StageTimer — 阶段计时器

记录单个阶段的开始、结束和耗时。

**属性：**`.name`、`.elapsed`、`.elapsed_str`、`.is_done`

**方法：**`start()`、`stop()`、`add_bytes(n)`

#### PipelineTimer — 流水线时间轴

追踪所有阶段的耗时，最后打印时间轴总览。

**构造函数：`PipelineTimer(label="")`**

```python
timer = PipelineTimer("EP01")
with timer.stage("1.提取音轨"):
    extract_audio()
with timer.stage("2.HEVC 编码"):
    encode_hevc()
timer.summary()
# ─── 流水线时间轴 [EP01] ───
#    1. ✅ 1.提取音轨          3.2s  [██░░░░]   5.1%
#    2. ✅ 2.HEVC 编码        58.4s  [██████]  93.8%
#                      总计: 1m2s
```

**方法：**

- `stage(name)` — 上下文管理器，自动开始/结束阶段。
- `stage_start(name)` — 手动开始（需调用 `.stop()`）。
- `summary(width=50)` — 打印时间轴总览。

---

### _backup.py — 自动备份

```python
from bmlsub import backup_if_exists
```

#### backup_if_exists

**`backup_if_exists(file_path, suffix=None)` → `Path | None`：**

- `file_path`：要备份的文件路径。
- `suffix`：自定义时间戳后缀，`None` = 自动生成 `YYYYMMDD_HHMMSS`。
- 若文件存在 → 移动到 `_backup/` 目录，文件名加时间戳（如 `01_HEVC10bit_20260710_143000.mkv`）。
- 若文件不存在 → 返回 `None`。

所有模块在覆盖已有文件前（编码、封装、种子生成等）都会自动调用此函数，确保旧文件不会丢失。

---

## 凭证配置

所有凭证按优先级：**构造函数参数 > 环境变量 > `~/.config/bml/*.json`**

### R2 (`~/.config/bml/r2_config.json`)

```json
{
    "account_id": "...",
    "access_key_id": "...",
    "secret_access_key": "...",
    "bucket_name": "bml",
    "endpoint": "https://<account_id>.r2.cloudflarestorage.com"
}
```

环境变量：`R2_ACCOUNT_ID` `R2_ACCESS_KEY_ID` `R2_SECRET_ACCESS_KEY` `R2_BUCKET_NAME` `R2_ENDPOINT`

### qBittorrent (`~/.config/bml/qb_config.json`)

```json
{
    "host": "localhost",
    "port": 8081,
    "username": "admin",
    "password": "...",
    "download_base": "/path/to/downloads"
}
```

环境变量：`QB_HOST` `QB_PORT` `QB_USERNAME` `QB_PASSWORD` `QB_DOWNLOAD_BASE`

### anibt.net (`~/.config/bml/anibt_config.json`)

```json
{
    "token": "...",
    "api_url": "https://anibt.net/api/releases/publish"
}
```

环境变量：`ANIBT_TOKEN` `ANIBT_API_URL`

---

## 常见问题

**VideoToolbox 编码失败？**
确保在 Mac 上运行，`ffmpeg -encoders | grep videotoolbox` 确认支持。非 Mac 环境请使用 `Encoder.encode_x264()`。

**种子格式选择？**
anibt.net（动漫花园）兼容需要 `v1_only=True`。其他 tracker 可用默认的 v1+v2 hybrid。

**R2 上传失败？**
检查凭证文件 `~/.config/bml/r2_config.json` 存在且 API Token 有 Object Read & Write 权限。也确认 bucket 名称和 account_id 正确。

**mkvpropedit 不可用？**
`Encoder.strip_metadata()` 会自动回退到 ffmpeg 流拷贝方式清理元数据。

**如何只运行部分阶段？**
每个模块都独立可用，直接在 Python/Notebook 中 import 需要的模块即可。也可以使用 `Pipeline.process_episode()` 的 `skip_*` 参数跳过不需要的阶段。

**远程做种时 SSH 横幅污染？**
`RemoteSeeder._parse_add_response()` 已内置 SSH MOTD 横幅过滤，自动从混入输出中提取 JSON 响应。
