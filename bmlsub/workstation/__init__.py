"""Stable Python API for episode workstation orchestration."""

from .common import Workstation, open_workstation
from .delivery import (
    plan_delivery, plan_delivery_execution, run_delivery, run_delivery_step,
    validate_translation_delivery,
)
from .models import (
    DeliveryConfig, DeliverySelection, PreprocessConfig, PublishConfig, TrackSelection,
    TranscriptionJob, WorkstationConfig, transcription_jobs_for_mode,
)
from .naming import ProductKind, ReleaseNames, product_filename, product_path, product_torrent_path
from .preprocess import plan_preprocess, run_preprocess, run_preprocess_step
from .publish import plan_publish, run_publish, run_publish_step
from .series import (
    SERIES_SCHEMA_VERSION, SeriesContext, SeriesMetadata, create_series_metadata,
    discover_series_context, ensure_traditional_series_names, prompt_series_metadata,
    series_metadata_questions,
    series_metadata_template, update_series_publish_config, write_series_metadata_template,
)
from .start import (
    discover_episode_directories, execute_recommended_action, inspect_episode_stage,
    inspect_series_workspace, plan_rebuild, resolve_series_root, run_rebuild,
)
from .state import load_status

__all__ = [
    "DeliveryConfig", "DeliverySelection", "PreprocessConfig", "ProductKind", "PublishConfig", "ReleaseNames",
    "SERIES_SCHEMA_VERSION", "SeriesContext", "SeriesMetadata", "create_series_metadata",
    "ensure_traditional_series_names", "prompt_series_metadata", "series_metadata_questions", "series_metadata_template",
    "update_series_publish_config",
    "write_series_metadata_template", "discover_episode_directories",
    "execute_recommended_action", "inspect_episode_stage", "inspect_series_workspace",
    "plan_rebuild", "resolve_series_root", "run_rebuild",
    "TrackSelection", "TranscriptionJob", "transcription_jobs_for_mode", "Workstation", "WorkstationConfig", "load_status",
    "open_workstation", "discover_series_context", "plan_delivery", "plan_delivery_execution", "plan_preprocess", "plan_publish", "product_filename",
    "product_path", "product_torrent_path", "run_delivery", "run_delivery_step",
    "run_preprocess", "run_preprocess_step", "run_publish", "run_publish_step",
    "validate_translation_delivery",
]
