"""
BML 动漫处理流水线

用法:
    pip install bml-subpro

    from bmlsub import Pipeline, PipelineConfig

    config = PipelineConfig(work_dir=".")
    pipeline = Pipeline(config)
    pipeline.extract_media(episodes=["01"])

"""

from .config import (
    PipelineConfig,
    EncodePreset,
    SubtitleStandard,
    PRESET_HEVC_VT_DEFAULT,
    PRESET_X264_SLOW,
    PRESET_X264_VERYSLOW,
    SUB_STANDARD_HD,
)

from .media import MediaExtractor, ExtractedTrack, PreferredSubs, SubtitleInfo
from .transcribe import Transcriber, TranscriptionError, model_short_name
from .encode import Encoder
from .subtitle import SubtitleValidator
from .package import Packager, PackagingError
from .transfer import (
    Transfer, TransferError,
    SSHConnectionError, HashVerificationError, CrocTransferError,
)
from .publish import Publisher, PublishError
from .r2upload import R2Uploader, R2UploadError
from .seeder import RemoteSeeder, SeederError
from .torrent import TorrentCreator, DEFAULT_TRACKERS
from .scan import (
    scan_products, check_products, product_path, product_torrent_path,
    PRODUCT_FORMATS,
)
from .pipeline import Pipeline

# 工具模块
from .progress import (
    ProgressBar,
    SpeedMeter,
    StageTimer,
    PipelineTimer,
)
from .model_utils import (
    detect_platform,
    is_apple_silicon,
    get_recommended_models,
    check_model_available,
    download_model,
    resolve_model,
    list_cached_models,
    print_model_guide,
    ModelRecommendation,
    ResolvedModel,
)
from ._backup import backup_if_exists

__all__ = [
    # Config
    "PipelineConfig",
    "EncodePreset",
    "SubtitleStandard",
    "PRESET_HEVC_VT_DEFAULT",
    "PRESET_X264_SLOW",
    "PRESET_X264_VERYSLOW",
    "SUB_STANDARD_HD",
    # Core modules
    "MediaExtractor",
    "ExtractedTrack",
    "PreferredSubs",
    "SubtitleInfo",
    "Transcriber",
    "model_short_name",
    "Encoder",
    "SubtitleValidator",
    "Packager",
    "Transfer",
    "Publisher",
    "TorrentCreator",
    "DEFAULT_TRACKERS",
    "scan_products",
    "check_products",
    "product_path",
    "product_torrent_path",
    "PRODUCT_FORMATS",
    "R2Uploader",
    "RemoteSeeder",
    "Pipeline",
    # Errors
    "TranscriptionError",
    "TransferError",
    "SSHConnectionError",
    "HashVerificationError",
    "CrocTransferError",
    "PackagingError",
    "PublishError",
    "R2UploadError",
    "SeederError",
    # Model management
    "detect_platform",
    "is_apple_silicon",
    "get_recommended_models",
    "check_model_available",
    "download_model",
    "resolve_model",
    "list_cached_models",
    "print_model_guide",
    "ModelRecommendation",
    "ResolvedModel",
    # Utilities
    "ProgressBar",
    "SpeedMeter",
    "StageTimer",
    "PipelineTimer",
    "backup_if_exists",
]
