# 从 GitHub Release 安装

Release 安装适合需要固定版本、可重复部署或核对安装包摘要的用户。当前已发布稳定版为
[`v1.3.1`](https://github.com/microseventh/bmlsub/releases/tag/v1.3.1)。

仓库源码当前版本为 `1.3.2`；该版本尚未发布 Release。需要安装当前源码版时，
使用[快速开始](quickstart.md)中的 Git 安装命令。

## 准备环境和系统依赖

bmlsub 需要 Python 3.10 或更高版本。推荐使用独立 Conda 环境：

```bash
conda create -n bmlsub python=3.12
conda activate bmlsub
brew install homebrew-ffmpeg/ffmpeg/ffmpeg
brew install mkvtoolnix
python -m pip install --upgrade pip
```

## 直接安装 Release wheel

使用 Release 页面中经过验证的通用 wheel：

```bash
python -m pip install "https://github.com/microseventh/bmlsub/releases/download/v1.3.1/bmlsub-1.3.1-py3-none-any.whl"
bmlsub --version
```

版本输出应为：

```text
bmlsub 1.3.1
```

Apple Silicon 上需要 MLX Whisper 转录时，可在安装 URL 前声明可选依赖：

```bash
python -m pip install "bmlsub[transcription] @ https://github.com/microseventh/bmlsub/releases/download/v1.3.1/bmlsub-1.3.1-py3-none-any.whl"
```

## 校验后安装

需要先核对文件时，下载 wheel 并验证 Release 中公布的 SHA-256：

```bash
curl -LO "https://github.com/microseventh/bmlsub/releases/download/v1.3.1/bmlsub-1.3.1-py3-none-any.whl"
echo "518b06a60f32634caec0b31f0c3c304ab1107e9f2cbf1932d5e2c75cd49542c6  bmlsub-1.3.1-py3-none-any.whl" | shasum -a 256 -c -
python -m pip install ./bmlsub-1.3.1-py3-none-any.whl
```

使用同一份已校验 wheel 并安装 MLX Whisper 可选依赖：

```bash
python -m pip install "./bmlsub-1.3.1-py3-none-any.whl[transcription]"
```

源码包也作为 Release 资产提供：

```text
bmlsub-1.3.1.tar.gz
SHA-256: 6f78de650f5856db27daae339b289898481b91eb76a261b9f5f627eb67ae4a31
```

应优先使用 Release 页面列出的 wheel 或源码包，不要把 GitHub 自动生成的
`Source code (zip)` / `Source code (tar.gz)` 当作经过上述摘要验证的发布资产。

## 升级或重新安装

升级到该 Release：

```bash
python -m pip install --upgrade "https://github.com/microseventh/bmlsub/releases/download/v1.3.1/bmlsub-1.3.1-py3-none-any.whl"
```

需要覆盖当前安装时：

```bash
python -m pip install --force-reinstall "https://github.com/microseventh/bmlsub/releases/download/v1.3.1/bmlsub-1.3.1-py3-none-any.whl"
```

安装完成后运行 `bmlsub --help` 和 `bmlsub --version` 确认命令入口与版本。
