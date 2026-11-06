"""Server-side feature toggles (``tenants.settings.features``, review-v5 §3)."""

from __future__ import annotations

from typing import Any

from app.core.errors import ApiError
from app.core.settings_v5 import section


class FeatureDisabled(ApiError):
    """This feature is turned off"""

    status, code = 403, "feature_disabled"


def feature_on(ts: dict[str, Any] | None, name: str) -> bool:
    return bool(section(ts, "features").get(name, False))


def require_feature(ts: dict[str, Any] | None, name: str) -> None:
    """Raise 403 ``feature_disabled`` when the toggle is off (unknown names are off)."""
    if not feature_on(ts, name):
        raise FeatureDisabled(f"Feature '{name}' is disabled")
