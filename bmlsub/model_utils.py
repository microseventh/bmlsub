"""
平台检测 & 模型管理 — 自动检测运行平台，推荐/检查/下载转录模型

用法:
    from bmlsub.model_utils import (
        detect_platform, is_apple_silicon, get_recommended_models,
        check_model_available, download_model, resolve_model,
        list_cached_models, print_model_guide,
    )

    # 快速上手：一行搞定模型选择 + 检查 + 下载引导
    info = resolve_model(language="ja")
    # → {"model_id": "...", "backend": "mlx", "available": True, ...}

设计原则:
    - macOS Apple Silicon → MLX Whisper（硬件加速，最快）
    - macOS Intel / Linux / Windows → faster-whisper（CTranslate2，跨平台最快）
    - 每个平台给出推荐模型 + 下载指引 + 自定义路径说明
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from dataclasses import dataclass, field


# ═══════════════════════════════════════════════════════════════
# 平台检测
# ═══════════════════════════════════════════════════════════════

def detect_platform() -> dict:
    """
    检测当前运行平台信息。

    Returns
    -------
    dict:
        system       — 'Darwin' | 'Linux' | 'Windows'
        machine      — 'arm64' | 'x86_64' | ...
        is_macos     — 是否 macOS
        is_apple_silicon — 是否 Apple Silicon (arm64 Mac)
        python_version
    """
    sys_name = platform.system()
    machine = platform.machine()
    return {
        "system": sys_name,
        "machine": machine,
        "is_macos": sys_name == "Darwin",
        "is_apple_silicon": (sys_name == "Darwin" and machine == "arm64"),
        "python_version": sys.version,
    }


def is_apple_silicon() -> bool:
    """快捷方法：当前是否为 Apple Silicon Mac"""
    return platform.system() == "Darwin" and platform.machine() == "arm64"


# ═══════════════════════════════════════════════════════════════
# 推荐模型配置
# ═══════════════════════════════════════════════════════════════

@dataclass
class ModelRecommendation:
    """单个模型的推荐信息"""
    model_id: str                                # HF repo 路径 或本地路径
    backend: str                                 # 'mlx' | 'faster_whisper' | 'openai'
    name: str                                    # 人类可读简称
    description: str                             # 说明
    speed: str                                   # '最快' | '快' | '中' | '慢'
    accuracy: str                                # '最高' | '高' | '中'
    lang_specialty: str                          # 语言专长
    size_gb: float                               # 模型大约大小 (GB)
    install_cmd: str                             # pip install 命令
    cache_dir_help: str                          # 缓存位置说明


# 推荐模型表（按平台 + 语言）
_RECOMMENDED_MODELS: dict[str, list[ModelRecommendation]] = {
    # ── macOS Apple Silicon ──
    "macos_arm64_ja": [
        ModelRecommendation(
            model_id="mlx-community/kotoba-whisper-v2.0-8bit",
            backend="mlx",
            name="kotoba-whisper-v2.0 (8bit)",
            description="日语专用模型，精度最高，8bit 量化平衡速度与精度",
            speed="快",
            accuracy="最高",
            lang_specialty="日语专用",
            size_gb=1.5,
            install_cmd="pip install mlx-whisper",
            cache_dir_help="~/.cache/huggingface/hub/",
        ),
        ModelRecommendation(
            model_id="mlx-community/whisper-large-v3-turbo",
            backend="mlx",
            name="whisper-large-v3-turbo",
            description="通用大模型 Turbo 版，速度快精度高",
            speed="最快",
            accuracy="高",
            lang_specialty="多语言通用",
            size_gb=1.6,
            install_cmd="pip install mlx-whisper",
            cache_dir_help="~/.cache/huggingface/hub/",
        ),
        ModelRecommendation(
            model_id="mlx-community/whisper-medium-mlx",
            backend="mlx",
            name="whisper-medium",
            description="中等模型，适合分割转录（chunked 方式推荐）",
            speed="中",
            accuracy="中",
            lang_specialty="多语言通用",
            size_gb=1.5,
            install_cmd="pip install mlx-whisper",
            cache_dir_help="~/.cache/huggingface/hub/",
        ),
    ],
    "macos_arm64_general": [
        ModelRecommendation(
            model_id="mlx-community/whisper-large-v3-turbo",
            backend="mlx",
            name="whisper-large-v3-turbo",
            description="通用大模型 Turbo 版，速度快精度高，多语言首选",
            speed="最快",
            accuracy="高",
            lang_specialty="多语言通用",
            size_gb=1.6,
            install_cmd="pip install mlx-whisper",
            cache_dir_help="~/.cache/huggingface/hub/",
        ),
        ModelRecommendation(
            model_id="mlx-community/whisper-medium-mlx",
            backend="mlx",
            name="whisper-medium",
            description="中等模型，适合分割转录",
            speed="中",
            accuracy="中",
            lang_specialty="多语言通用",
            size_gb=1.5,
            install_cmd="pip install mlx-whisper",
            cache_dir_help="~/.cache/huggingface/hub/",
        ),
    ],
    # ── 非 Apple Silicon（macOS Intel / Linux / Windows）──
    "other_ja": [
        ModelRecommendation(
            model_id="deepdml/faster-whisper-large-v3-turbo-ct2",
            backend="faster_whisper",
            name="faster-whisper-large-v3-turbo (CTranslate2)",
            description="faster-whisper 版 large-v3-turbo，CTranslate2 加速，跨平台最快",
            speed="最快",
            accuracy="高",
            lang_specialty="多语言通用",
            size_gb=1.6,
            install_cmd="pip install faster-whisper",
            cache_dir_help="~/.cache/huggingface/hub/",
        ),
        ModelRecommendation(
            model_id="Systran/faster-whisper-large-v3",
            backend="faster_whisper",
            name="faster-whisper-large-v3",
            description="large-v3 CTranslate2 版，精度最高",
            speed="中",
            accuracy="最高",
            lang_specialty="多语言通用",
            size_gb=2.9,
            install_cmd="pip install faster-whisper",
            cache_dir_help="~/.cache/huggingface/hub/",
        ),
    ],
    "other_general": [
        ModelRecommendation(
            model_id="deepdml/faster-whisper-large-v3-turbo-ct2",
            backend="faster_whisper",
            name="faster-whisper-large-v3-turbo (CTranslate2)",
            description="faster-whisper 版 large-v3-turbo，CTranslate2 加速，跨平台最快",
            speed="最快",
            accuracy="高",
            lang_specialty="多语言通用",
            size_gb=1.6,
            install_cmd="pip install faster-whisper",
            cache_dir_help="~/.cache/huggingface/hub/",
        ),
    ],
}


def get_recommended_models(language: str = "ja") -> list[ModelRecommendation]:
    """
    根据当前平台和语言返回推荐模型列表（按优先级排序）。

    Parameters
    ----------
    language : 音频语言代码。'ja' = 日语 → 包含日语专用模型推荐；其他 = 通用推荐。

    Returns
    -------
    list[ModelRecommendation] — 推荐模型列表，第 0 个为首选
    """
    plat = detect_platform()
    is_ja = language.lower() in ("ja", "jpn", "jp", "japanese")

    if plat["is_apple_silicon"]:
        key = "macos_arm64_ja" if is_ja else "macos_arm64_general"
    else:
        key = "other_ja" if is_ja else "other_general"

    return _RECOMMENDED_MODELS.get(key, _RECOMMENDED_MODELS["other_general"])


# ═══════════════════════════════════════════════════════════════
# 模型缓存检查
# ═══════════════════════════════════════════════════════════════

def _hf_cache_dir() -> Path:
    """返回 HuggingFace 缓存目录"""
    env = os.environ.get("HF_HOME") or os.environ.get("HUGGINGFACE_HUB_CACHE")
    if env:
        return Path(env)
    return Path.home() / ".cache" / "huggingface" / "hub"


def _mlx_model_cache_path(model_id: str) -> Path | None:
    """
    检查 MLX 模型是否在 HF 缓存中。

    MLX 模型在缓存中以 snapshots/<hash>/ 形式存在，
    包含 *.safetensors、config.json 等文件。
    """
    cache_dir = _hf_cache_dir()
    # MLX 模型路径格式: models--org--repo-name
    safe_name = "models--" + model_id.replace("/", "--")
    model_dir = cache_dir / safe_name

    if not model_dir.exists():
        return None

    # 找 snapshots 下的实际模型文件
    snapshots = model_dir / "snapshots"
    if not snapshots.exists():
        return None

    # 找最新的 snapshot（通常只有一个）
    snapshot_dirs = sorted(snapshots.iterdir(), reverse=True)
    for sd in snapshot_dirs:
        if sd.is_dir():
            # MLX 模型通常有 .safetensors 或 model.safetensors
            has_weights = any(
                sd.glob("*.safetensors")
            ) or (sd / "model.safetensors").exists()
            if has_weights:
                return sd
    return None


def _faster_whisper_model_cache_path(model_id: str) -> Path | None:
    """
    检查 faster-whisper (CTranslate2) 模型是否在缓存中。

    faster-whisper 也会缓存到 HF hub 目录，模型文件以 model.bin 等形式存在。
    """
    cache_dir = _hf_cache_dir()
    safe_name = "models--" + model_id.replace("/", "--")
    model_dir = cache_dir / safe_name

    if not model_dir.exists():
        # 也检查 CTranslate2 专用缓存路径
        ct2_cache = Path.home() / ".cache" / "faster_whisper"
        safe_name_ct2 = model_id.replace("/", "_")
        alt_dir = ct2_cache / safe_name_ct2
        if alt_dir.exists():
            has_model = any(alt_dir.glob("model*"))
            if has_model:
                return alt_dir
        return None

    snapshots = model_dir / "snapshots"
    if not snapshots.exists():
        return None

    for sd in sorted(snapshots.iterdir(), reverse=True):
        if sd.is_dir():
            # CTranslate2 模型文件: model.bin, config.json, tokenizer.json 等
            has_model = any(sd.glob("model*")) or (sd / "model.bin").exists()
            if has_model:
                return sd
    return None


def check_model_available(model_id: str, backend: str = "auto") -> bool:
    """
    检查指定模型是否已下载到本地缓存。

    Parameters
    ----------
    model_id : HF repo 路径（如 "mlx-community/whisper-large-v3-turbo"）
               或本地路径（如 "/path/to/model"）。
    backend : 'mlx' | 'faster_whisper' | 'auto'
              'auto' = 根据 model_id 前缀自动判断。

    Returns
    -------
    bool — True 表示模型已在本地缓存中
    """
    # 本地路径
    local = Path(model_id).expanduser()
    if local.exists() and local.is_dir():
        return any(local.glob("*.safetensors")) or any(local.glob("model*"))

    # 自动判断后端
    if backend == "auto":
        if "mlx-community" in model_id or model_id.startswith("mlx-"):
            backend = "mlx"
        elif "faster-whisper" in model_id or "ct2" in model_id.lower():
            backend = "faster_whisper"
        else:
            # 尝试两种都查
            return (
                _mlx_model_cache_path(model_id) is not None
                or _faster_whisper_model_cache_path(model_id) is not None
            )

    if backend == "mlx":
        return _mlx_model_cache_path(model_id) is not None
    elif backend == "faster_whisper":
        return _faster_whisper_model_cache_path(model_id) is not None
    else:
        return _mlx_model_cache_path(model_id) is not None


def list_cached_models() -> list[str]:
    """
    列出本地已缓存的转录模型。

    扫描 HF 缓存目录，找出已下载的 MLX Whisper / faster-whisper 模型。

    Returns
    -------
    list[str] — 模型 repo ID 列表（如 ["mlx-community/whisper-large-v3-turbo", ...]）
    """
    cache_dir = _hf_cache_dir()
    if not cache_dir.exists():
        return []

    found: list[str] = []
    for entry in sorted(cache_dir.iterdir()):
        if not entry.is_dir() or not entry.name.startswith("models--"):
            continue
        # 还原 repo ID: models--org--repo → org/repo
        repo = entry.name[len("models--"):].replace("--", "/")
        # 还原被双连字符编码的斜杠（如果 org/repo 中出现过 / 会变成 --）
        # 实际 huggingface 缓存规范: models--org--repo-name
        snapshots = entry / "snapshots"
        if snapshots.exists() and any(snapshots.iterdir()):
            found.append(repo)

    return found


# ═══════════════════════════════════════════════════════════════
# 模型下载
# ═══════════════════════════════════════════════════════════════

def download_model(model_id: str, backend: str = "auto",
                   force: bool = False) -> bool:
    """
    下载转录模型到本地缓存。

    Parameters
    ----------
    model_id : HF repo 路径。
    backend : 'mlx' | 'faster_whisper' | 'auto'
              'auto' = 根据 model_id 前缀自动判断下载方式。
    force : True = 强制重新下载。

    Returns
    -------
    bool — True = 下载成功 / 已存在

    下载方式:
        MLX 模型: 通过 huggingface_hub.snapshot_download() 下载
        faster-whisper 模型: 通过 huggingface_hub.snapshot_download()
                              或 faster_whisper 自动下载
    """
    if not force and check_model_available(model_id, backend=backend):
        print(f"✅ 模型已存在: {model_id}")
        return True

    if backend == "auto":
        if "mlx-community" in model_id or model_id.startswith("mlx-"):
            backend = "mlx"
        elif "faster-whisper" in model_id or "ct2" in model_id.lower():
            backend = "faster_whisper"
        else:
            backend = "mlx"

    print(f"📥 正在下载模型: {model_id}")
    print(f"   后端: {backend}")
    print(f"   缓存位置: {_hf_cache_dir()}")

    try:
        from huggingface_hub import snapshot_download

        # 根据后端类型设置 ignore_patterns
        if backend == "mlx":
            # MLX 模型只需要 safetensors + config
            ignore_patterns = ["*.bin", "*.pt", "*.pth", "pytorch_model*",
                              "tf_model*", "flax_model*"]
        else:
            ignore_patterns = None

        snapshot_download(
            repo_id=model_id,
            ignore_patterns=ignore_patterns,
            resume_download=True,
        )
        print(f"✅ 下载完成: {model_id}")

        # 验证
        if check_model_available(model_id, backend=backend):
            return True
        else:
            print(f"⚠️ 下载完成但验证失败，请手动检查: {model_id}")
            return False

    except ImportError:
        print(f"❌ 缺少 huggingface_hub 库")
        print(f"   请执行: pip install huggingface_hub")
        return False
    except Exception as e:
        print(f"❌ 下载失败: {e}")
        print(f"   请手动下载: huggingface-cli download {model_id}")
        return False


# ═══════════════════════════════════════════════════════════════
# 模型解析（核心入口）
# ═══════════════════════════════════════════════════════════════

@dataclass
class ResolvedModel:
    """模型解析结果"""
    model_id: str                           # 最终使用的模型 ID 或路径
    backend: str                            # 'mlx' | 'faster_whisper' | 'openai'
    available: bool                         # 模型是否在本地可用
    cache_path: str | None                  # 本地缓存路径（如有）
    platform_info: dict                     # detect_platform() 返回值
    recommendation: ModelRecommendation | None  # 匹配到的推荐（None=用户自定义）
    notes: list[str] = field(default_factory=list)  # 额外提示


def resolve_model(model_id: str | None = None,
                  language: str = "ja",
                  backend: str | None = None,
                  auto_download: bool = False) -> ResolvedModel:
    """
    解析并验证转录模型，带完整指引。

    这是「阶段 2」模型选择的核心入口。根据平台、语言、用户指定
    自动确定最佳模型，并给出下载/安装指引。

    Parameters
    ----------
    model_id : 模型 ID 或本地路径。
               None  = 自动选择推荐模型。
               HF 路径 = 如 "mlx-community/whisper-large-v3-turbo"。
               本地路径 = 如 "/Users/me/models/my-whisper"。
    language : 音频语言代码，默认 "ja"。
    backend : 强制指定后端（'mlx' | 'faster_whisper'）。
              None = 根据模型 ID 和平台自动判断。
    auto_download : True = 模型不可用时自动下载（需 huggingface_hub）。
                    默认 False，只提示引导。

    Returns
    -------
    ResolvedModel — 包含模型 ID、可用性、缓存路径、平台信息和建议

    Examples
    --------
    >>> # 自动选择最佳模型
    >>> info = resolve_model(language="ja")
    >>> print(info.model_id)    # "mlx-community/kotoba-whisper-v2.0-8bit"
    >>> print(info.available)   # True/False

    >>> # 指定模型 + 自动下载
    >>> info = resolve_model(
    ...     "mlx-community/whisper-large-v3-turbo",
    ...     auto_download=True,
    ... )
    """
    plat = detect_platform()
    notes: list[str] = []

    # ── 1. 确定 backend ──
    resolved_backend = backend
    if resolved_backend is None:
        if model_id and Path(model_id).expanduser().exists():
            # 本地路径 → 让用户自行确认后端
            resolved_backend = "mlx" if plat["is_apple_silicon"] else "faster_whisper"
        elif plat["is_apple_silicon"]:
            resolved_backend = "mlx"
        else:
            resolved_backend = "faster_whisper"

    # 验证 backend 与平台兼容性
    if resolved_backend == "mlx" and not plat["is_macos"]:
        notes.append("⚠️ MLX Whisper 仅支持 macOS。非 macOS 平台请使用 faster-whisper。")
        resolved_backend = "faster_whisper"

    # ── 2. 确定 model_id ──
    recommendation: ModelRecommendation | None = None

    if model_id is None:
        # 自动选择推荐
        recs = get_recommended_models(language)
        if recs:
            recommendation = recs[0]
            resolved_model_id = recommendation.model_id
            notes.append(f"💡 自动选择推荐模型: {recommendation.name}")
            notes.append(f"   {recommendation.description}")
        else:
            # fallback
            resolved_model_id = "mlx-community/whisper-large-v3-turbo"
            notes.append("⚠️ 无推荐模型，使用默认 fallback")
    else:
        resolved_model_id = model_id
        # 检查是否匹配已知推荐
        for key, recs in _RECOMMENDED_MODELS.items():
            for r in recs:
                if r.model_id == model_id:
                    recommendation = r
                    break

    # ── 3. 检查可用性 ──
    local_path = Path(resolved_model_id).expanduser()
    is_local = local_path.exists() and local_path.is_dir()
    available = check_model_available(resolved_model_id, backend=resolved_backend)
    cache_path: str | None = None

    if is_local:
        available = True
        cache_path = str(local_path)
    elif available:
        cp = (
            _mlx_model_cache_path(resolved_model_id)
            if resolved_backend == "mlx"
            else _faster_whisper_model_cache_path(resolved_model_id)
        )
        cache_path = str(cp) if cp else None

    # ── 4. 生成指引 ──
    if not available:
        notes.append(f"📥 模型未下载: {resolved_model_id}")
        notes.append(f"   调用 download_model('{resolved_model_id}') 下载")
        notes.append(f"   或: huggingface-cli download {resolved_model_id}")

        if recommendation and recommendation.install_cmd:
            notes.append(f"   确保已安装依赖: {recommendation.install_cmd}")

        if plat["is_apple_silicon"]:
            notes.append("   💡 在 Apple Silicon Mac 上，MLX 模型首次使用时会自动下载")
            notes.append("      直接调用 Transcriber.transcribe_direct() 即可触发自动下载")
    else:
        notes.append(f"✅ 模型已就绪: {resolved_model_id}")
        if cache_path:
            notes.append(f"   缓存路径: {cache_path}")

    # 非推荐模型的提示
    if recommendation is None and not is_local:
        notes.append(f"ℹ️ 自定义模型路径/ID，确保兼容当前后端 ({resolved_backend})")
        if resolved_backend == "mlx":
            notes.append("   MLX 模型需为 mlx-community 发布的 safetensors 格式")
        elif resolved_backend == "faster_whisper":
            notes.append("   faster-whisper 模型需为 CTranslate2 格式")

    return ResolvedModel(
        model_id=resolved_model_id,
        backend=resolved_backend,
        available=available,
        cache_path=cache_path,
        platform_info=plat,
        recommendation=recommendation,
        notes=notes,
    )


# ═══════════════════════════════════════════════════════════════
# 指引打印
# ═══════════════════════════════════════════════════════════════

def print_model_guide(language: str = "ja") -> None:
    """
    打印完整的模型选择指引，包含：
    - 当前平台信息
    - 推荐模型列表
    - 每个模型的下载/安装说明
    - 已缓存模型列表

    Parameters
    ----------
    language : 音频语言代码
    """
    plat = detect_platform()
    recs = get_recommended_models(language)
    cached = list_cached_models()

    print("=" * 60)
    print("📋 转录模型选择指引")
    print("=" * 60)

    # 平台
    print(f"\n🖥️  当前平台:")
    print(f"   系统: {plat['system']} ({plat['machine']})")
    print(f"   Apple Silicon: {'✅ 是' if plat['is_apple_silicon'] else '❌ 否'}")
    print(f"   推荐后端: {'MLX Whisper' if plat['is_apple_silicon'] else 'faster-whisper (CTranslate2)'}")

    # 已缓存
    if cached:
        print(f"\n📦 已缓存模型 ({len(cached)} 个):")
        for m in cached:
            print(f"   ✅ {m}")
    else:
        print(f"\n📦 已缓存模型: (无)")

    # 推荐
    print(f"\n🎯 推荐模型 (语言: {language}):")
    print("-" * 60)
    for i, r in enumerate(recs):
        label = "🥇 首选" if i == 0 else f"  备选"
        print(f"  {label}: {r.name}")
        print(f"         模型: {r.model_id}")
        print(f"         后端: {r.backend}")
        print(f"         速度: {r.speed}  |  精度: {r.accuracy}  |  语言: {r.lang_specialty}")
        print(f"         大小: ~{r.size_gb} GB")
        print(f"         说明: {r.description}")
        print(f"         安装: {r.install_cmd}")
        print(f"         缓存: {r.cache_dir_help}")
        print()

    # 快速上手
    print("-" * 60)
    print("🚀 快速上手:")
    print()
    print("  from bmlsub.model_utils import resolve_model, download_model")
    print()
    print("  # 1. 自动选择 + 检查")
    print(f"  info = resolve_model(language='{language}')")
    print("  print(info.model_id)   # 推荐模型")
    print("  print(info.available)  # 是否已下载")
    print()
    print("  # 2. 下载（如未缓存）")
    print("  if not info.available:")
    print(f"      download_model(info.model_id)")
    print()
    print("  # 3. 或指定模型")
    print("  info = resolve_model('mlx-community/whisper-large-v3-turbo')")
    print()
    print("  # 4. 配合 Transcriber 使用")
    print("  from bmlsub import Transcriber")
    print("  t = Transcriber(model=info.model_id, language='ja')")
    print("  t.transcribe_direct(audio_path)")
    print()
    print("-" * 60)
    print("💡 提示:")
    if plat["is_apple_silicon"]:
        print("   MLX Whisper 首次调用 transcribe() 时会自动下载模型。")
        print("   你也可以提前下载，避免首次使用等待。")
    else:
        print("   faster-whisper 首次加载模型时会自动下载。")
        print("   也可以调用 download_model() 提前下载。")

    # 自定义路径
    print()
    print("📁 自定义模型路径:")
    print('   info = resolve_model("/path/to/my-model")')
    print("   支持本地路径，跳过下载检查。")
    print("=" * 60)


# ═══════════════════════════════════════════════════════════════
# 显示当前已有缓存模型
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print_model_guide()
