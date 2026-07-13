"""
平台检测与 MLX 模型管理
"""

from __future__ import annotations

import os
import platform
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ModelRecommendation:
    model_id: str
    backend: str
    name: str
    description: str
    speed: str
    accuracy: str
    lang_specialty: str
    size_gb: float
    install_cmd: str
    cache_dir_help: str


@dataclass
class ResolvedModel:
    model_id: str
    backend: str
    available: bool
    cache_path: str | None
    platform_info: dict
    recommendation: ModelRecommendation | None
    notes: list[str] = field(default_factory=list)


def detect_platform() -> dict:
    sys_name = platform.system()
    machine = platform.machine()
    return {
        "system": sys_name,
        "machine": machine,
        "is_macos": sys_name == "Darwin",
        "is_apple_silicon": sys_name == "Darwin" and machine == "arm64",
        "python_version": sys.version,
    }


def is_apple_silicon() -> bool:
    return platform.system() == "Darwin" and platform.machine() == "arm64"


_RECOMMENDED_MODELS = {
    "macos_arm64_ja": [
        ModelRecommendation(
            model_id="mlx-community/kotoba-whisper-v2.0-8bit",
            backend="mlx",
            name="kotoba-whisper-v2.0 (8bit)",
            description="日语优先，适合 Apple Silicon + MLX",
            speed="快",
            accuracy="最高",
            lang_specialty="日语专用",
            size_gb=1.5,
            install_cmd="conda activate mlx && pip install mlx-whisper",
            cache_dir_help="~/.cache/huggingface/hub/",
        ),
        ModelRecommendation(
            model_id="mlx-community/whisper-large-v3-turbo",
            backend="mlx",
            name="whisper-large-v3-turbo",
            description="多语言通用，默认推荐",
            speed="最快",
            accuracy="高",
            lang_specialty="多语言通用",
            size_gb=1.6,
            install_cmd="conda activate mlx && pip install mlx-whisper",
            cache_dir_help="~/.cache/huggingface/hub/",
        ),
    ],
    "macos_arm64_general": [
        ModelRecommendation(
            model_id="mlx-community/whisper-large-v3-turbo",
            backend="mlx",
            name="whisper-large-v3-turbo",
            description="多语言通用，默认推荐",
            speed="最快",
            accuracy="高",
            lang_specialty="多语言通用",
            size_gb=1.6,
            install_cmd="conda activate mlx && pip install mlx-whisper",
            cache_dir_help="~/.cache/huggingface/hub/",
        ),
    ],
    "other_general": [
        ModelRecommendation(
            model_id="mlx-community/whisper-large-v3-turbo",
            backend="mlx",
            name="whisper-large-v3-turbo",
            description="非 Apple Silicon 环境请自行确认兼容性",
            speed="中",
            accuracy="高",
            lang_specialty="多语言通用",
            size_gb=1.6,
            install_cmd="conda activate mlx && pip install mlx-whisper",
            cache_dir_help="~/.cache/huggingface/hub/",
        ),
    ],
}


def get_recommended_models(language: str = "ja") -> list[ModelRecommendation]:
    plat = detect_platform()
    is_ja = language.lower() in ("ja", "jpn", "jp", "japanese")
    if plat["is_apple_silicon"]:
        key = "macos_arm64_ja" if is_ja else "macos_arm64_general"
    else:
        key = "other_general"
    return _RECOMMENDED_MODELS[key]


def _hf_cache_dir() -> Path:
    env = os.environ.get("HF_HOME") or os.environ.get("HUGGINGFACE_HUB_CACHE")
    if env:
        return Path(env)
    return Path.home() / ".cache" / "huggingface" / "hub"


def check_model_available(model_id: str, backend: str = "auto") -> bool:
    local = Path(model_id).expanduser()
    if local.exists() and local.is_dir():
        return any(local.glob("*.safetensors")) or any(local.glob("model*"))

    model_dir = _hf_cache_dir() / ("models--" + model_id.replace("/", "--"))
    snapshots = model_dir / "snapshots"
    if not snapshots.exists():
        return False

    for snapshot_dir in sorted(snapshots.iterdir(), reverse=True):
        if snapshot_dir.is_dir() and (
            any(snapshot_dir.glob("*.safetensors")) or (snapshot_dir / "model.safetensors").exists()
        ):
            return True
    return False


def list_cached_models() -> list[str]:
    cache_dir = _hf_cache_dir()
    if not cache_dir.exists():
        return []

    found: list[str] = []
    for entry in sorted(cache_dir.iterdir()):
        if not entry.is_dir() or not entry.name.startswith("models--"):
            continue
        snapshots = entry / "snapshots"
        if snapshots.exists() and any(snapshots.iterdir()):
            found.append(entry.name[len("models--"):].replace("--", "/"))
    return found


def download_model(model_id: str, backend: str = "auto", force: bool = False) -> bool:
    if not force and check_model_available(model_id, backend=backend):
        print(f"✅ 模型已存在: {model_id}")
        return True

    print(f"📥 正在下载模型: {model_id}")
    try:
        from huggingface_hub import snapshot_download

        snapshot_download(repo_id=model_id, resume_download=True)
        ok = check_model_available(model_id, backend=backend)
        print("✅ 下载完成" if ok else "⚠️ 下载完成但未验证通过")
        return ok
    except ImportError:
        print("❌ 缺少 huggingface_hub，请先安装")
        return False
    except Exception as e:
        print(f"❌ 下载失败: {e}")
        return False


def resolve_model(
    model_id: str | None = None,
    language: str = "ja",
    backend: str | None = None,
    auto_download: bool = False,
) -> ResolvedModel:
    plat = detect_platform()
    notes: list[str] = []
    recommendation = get_recommended_models(language=language)[0]
    model_id = model_id or recommendation.model_id
    backend = backend or recommendation.backend

    if backend == "mlx" and not plat["is_macos"]:
        notes.append("⚠️ 当前不是 macOS，MLX Whisper 可能不可用。")

    available = check_model_available(model_id, backend=backend)
    if not available and auto_download:
        available = download_model(model_id, backend=backend)

    cache_path = None
    cache_dir = _hf_cache_dir() / ("models--" + model_id.replace("/", "--"))
    if cache_dir.exists():
        cache_path = str(cache_dir)

    return ResolvedModel(
        model_id=model_id,
        backend=backend,
        available=available,
        cache_path=cache_path,
        platform_info=plat,
        recommendation=recommendation if model_id == recommendation.model_id else None,
        notes=notes,
    )


def print_model_guide(language: str = "ja") -> None:
    plat = detect_platform()
    print("=== 模型指引 ===")
    print(f"平台: {plat['system']} / {plat['machine']}")
    print("建议先切到 conda mlx 环境运行。")
    print("推荐模型:")
    for item in get_recommended_models(language=language):
        ok = "✅ 已缓存" if check_model_available(item.model_id) else "❌ 未缓存"
        print(f"- {item.name}: {item.model_id} | {item.description} | {ok}")
