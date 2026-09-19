"""Vendored SubsRefine processing core.

Source: https://github.com/MingYSub/SubsRefine/tree/c47cec799fb5615d74a6561a7b6a91054c669c4b
Commit: c47cec799fb5615d74a6561a7b6a91054c669c4b
Copyright (c) 2025 MingYSub
Licensed under the MIT License; see LICENSES/SubsRefine-MIT.txt.

Local changes are limited to package placement, Python 3.10-compatible
``StrEnum`` imports, and correction of the upstream repository URL constant.
"""

from .config import ProcessingConfig
from .constants import SCRIPT_VERSION
from .subtitle import Subtitle
from .processor import Processor
