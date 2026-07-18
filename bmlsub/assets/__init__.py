"""External source-asset registration, matching, and confirmation."""

from .matching import (
    MATCH_RULE_VERSION, episode_manifest, refresh_artifact,
    run_asset_matching, run_match_confirmation,
)
from .models import SourceAssetKind, SourceAssetRegistrationOptions
from .registration import SOURCE_INSPECTOR_VERSION, run_source_asset_registration

__all__ = [
    "MATCH_RULE_VERSION", "SOURCE_INSPECTOR_VERSION", "SourceAssetKind",
    "SourceAssetRegistrationOptions", "episode_manifest", "refresh_artifact",
    "run_asset_matching", "run_match_confirmation", "run_source_asset_registration",
]
