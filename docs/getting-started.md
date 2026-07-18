# Getting started

[中文](zh/getting-started.md) · [Documentation home](../README.md)

`bmlsub` targets macOS on Apple Silicon with Python 3.10 or newer. The recommended environment is Conda base.

## 1. Activate Python

```bash
conda activate base
python --version
```

## 2. Install system programs

pip installs all Python dependencies, but media and remote commands also call system executables.

### FFmpeg and ffprobe

Install the Homebrew FFmpeg tap formula used by this project:

```bash
brew install homebrew-ffmpeg/ffmpeg/ffmpeg
```

The formula provides both `ffmpeg` and `ffprobe`. Verify that Homebrew reports the tapped formula as installed and linked:

```bash
brew info homebrew-ffmpeg/ffmpeg/ffmpeg
```

Expected form:

```text
==> Installed Versions
homebrew-ffmpeg/ffmpeg/ffmpeg <version> (...) [Linked]
```

Then verify both executables:

```bash
ffmpeg -version
ffprobe -version
```

FFmpeg is used for audio/subtitle extraction, transcription slicing, HEVC encoding, and H.264 hardsubs. ffprobe is used for registration, track inspection, and output validation.

### MKVToolNix

```bash
brew install mkvtoolnix
mkvmerge --version
```

`mkvmerge` creates and identifies Matroska files for internal-subtitle delivery.

### SSH

Use the SSH client included with macOS; no Homebrew installation is required:

```bash
/usr/bin/ssh -V
ssh -V
```

SSH is used for remote pull, qBittorrent tunneling, and bounded remote probes.

## 3. Install bmlsub

The shortest supported command installs the code and every Python feature dependency:

```bash
python -m pip install git+https://github.com/microseventh/bmlsub.git
```

The default dependency set includes requests, fonttools, xxhash, MLX Whisper, libtorrent, boto3, and keyring. No extras selector is required.

Local editable installation:

```bash
cd /path/to/bmlsub
python -m pip install -e .
```

## 4. Verify the installation

```bash
bmlsub --version
bmlsub --help
command -v ffmpeg ffprobe mkvmerge ssh
```

Expected package version:

```text
bmlsub 1.0.0
```

If `ffmpeg` or `ffprobe` resolves unexpectedly, confirm the linked tap formula with `brew info homebrew-ffmpeg/ffmpeg/ffmpeg` and inspect `brew --prefix` and `PATH`.

## 5. Initialize a series

```bash
bmlsub workstation series create \
  --parent-dir /path/to/series-parent \
  --series-folder-name ExampleSeries \
  --title-chs 示例番组 --title-cht 示例番組 \
  --romanized-title ExampleSeries \
  --group-chs SimplifiedGroup --group-cht TraditionalGroup
```

Without `--parent-dir`, the target defaults to `~/Downloads/<series>/bgminfo/series.json`. Existing metadata is refused unless `--replace` is explicit; `--interactive` asks the same fields one by one.

## 6. Start an episode workspace

Most low-level commands default `--workspace` to the current directory and use `.bmlsub/state.sqlite3`. Workstation commands treat `--workspace` as a numeric episode directory and use `workstation/state/state.sqlite3` plus readable config, manifest, summary, step, and Artifact JSON snapshots.

```bash
bmlsub asset register-video \
  --workspace /path/to/series/01 \
  --episode-id 01 \
  --video /path/to/series/01/source.mkv \
  --purpose source \
  --purpose extract

bmlsub workstation preprocess --workspace /path/to/series/01 --episode-id 01
bmlsub workstation status --workspace /path/to/series/01
```

Preprocess auto-selects only a unique top-level source video. Delivery inherits release names and Production Profiles from the direct-parent `bgminfo/series.json`, requires one formal CHS/JPN ASS and top-level Aegisub-collected fonts, and allows explicit episode overrides. Publication remains inert until `--confirm-external-action` is supplied.
