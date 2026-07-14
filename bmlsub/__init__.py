"""
bmlsub — BML 动漫处理流水线优化版
"""

from .config import (
    PipelineConfig,
    EncodePreset,
    SubtitleStandard,
    SubtitleConversionConfig,
    ProductNaming,
    LanguageStrategy,
    TrackMetaConfig,
    ProjectNaming,
    ProjectConfig,
    PROJECT_CONFIG_FILENAME,
    project_config_path,
    load_project_config,
    save_project_config,
    WorkstationConfig,
    PRESET_HEVC_VT_DEFAULT,
    PRESET_X264_SLOW,
    PRESET_X264_VERYSLOW,
    SUB_STANDARD_HD,
    PRODUCT_FORMATS,
    parse_episode_ids,
)
from .media import MediaExtractor, ExtractedTrack, PreferredSubs, SubtitleInfo
from .transcribe import Transcriber, TranscriptionError, model_short_name
from .encode import Encoder
from .subtitle import SubtitleValidator, SubtitleConversionError
from .hanvert import (
    HanvertConversionError,
    classify_ass_language,
    convert_ass_with_fanhuaji,
    extract_ass_analysis,
    strip_ass_tags,
)
from .package import Packager, PackagingError
from .publish import Publisher, PublishError
from .r2upload import R2Uploader, R2UploadError
from .seeder import RemoteSeeder, SeederError
from .torrent import TorrentCreator, DEFAULT_TRACKERS, TorrentMetadata, read_torrent_metadata
from .scan import scan_products, check_products, product_path, product_torrent_path
from .episode import EpisodeFiles
from .pipeline import (
    Pipeline,
    StageStatus,
    EpisodeStagePlan,
    WorkstationStage0Summary,
    WorkstationBatchResult,
)
from .progress import ProgressBar, SpeedMeter, StageTimer, PipelineTimer
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
    'PipelineConfig', 'EncodePreset', 'SubtitleStandard', 'SubtitleConversionConfig', 'ProductNaming', 'LanguageStrategy',
    'TrackMetaConfig', 'ProjectNaming', 'ProjectConfig', 'PROJECT_CONFIG_FILENAME', 'project_config_path',
    'load_project_config', 'save_project_config', 'WorkstationConfig', 'PRESET_HEVC_VT_DEFAULT', 'PRESET_X264_SLOW',
    'PRESET_X264_VERYSLOW', 'SUB_STANDARD_HD', 'PRODUCT_FORMATS', 'parse_episode_ids',
    'MediaExtractor', 'ExtractedTrack', 'PreferredSubs', 'SubtitleInfo', 'Transcriber',
    'model_short_name', 'Encoder', 'SubtitleValidator', 'SubtitleConversionError',
    'HanvertConversionError', 'classify_ass_language', 'convert_ass_with_fanhuaji',
    'extract_ass_analysis', 'strip_ass_tags', 'Packager', 'Publisher',
    'TorrentCreator', 'DEFAULT_TRACKERS', 'TorrentMetadata', 'read_torrent_metadata',
    'scan_products', 'check_products', 'product_path', 'product_torrent_path', 'EpisodeFiles',
    'R2Uploader', 'RemoteSeeder', 'Pipeline', 'StageStatus', 'EpisodeStagePlan',
    'WorkstationStage0Summary', 'WorkstationBatchResult', 'TranscriptionError', 'PackagingError',
    'PublishError', 'R2UploadError', 'SeederError', 'ProgressBar', 'SpeedMeter', 'StageTimer',
    'PipelineTimer', 'detect_platform', 'is_apple_silicon', 'get_recommended_models',
    'check_model_available', 'download_model', 'resolve_model', 'list_cached_models',
    'print_model_guide', 'ModelRecommendation', 'ResolvedModel', 'backup_if_exists',
]
