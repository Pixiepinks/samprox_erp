from __future__ import annotations

from typing import Any, Mapping

from flask import current_app


def _normalize_profiles(raw_profiles: list[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    profiles: list[dict[str, Any]] = []
    for profile in raw_profiles or []:
        profiles.append(
            {
                "key": profile.get("key") or profile.get("id") or profile.get("slug"),
                "name": profile.get("name"),
                "address_lines": list(profile.get("address_lines") or []),
                "contact": profile.get("contact"),
                "tagline": profile.get("tagline"),
                "logo_path": profile.get("logo_path"),
            }
        )
    return [profile for profile in profiles if profile.get("name")]


def available_company_keys(app_config: Mapping[str, Any] | None = None) -> list[str]:
    """Return the configured company keys in priority order.

    Includes legacy single-company configuration values so existing deployments
    without ``COMPANY_PROFILES`` remain compatible.
    """

    config = app_config or getattr(current_app, "config", {})
    profiles = _normalize_profiles(config.get("COMPANY_PROFILES"))

    ordered_keys: list[str] = []
    for profile in profiles:
        key = profile.get("key")
        if key:
            ordered_keys.append(str(key))

    legacy_key = config.get("COMPANY_KEY")
    if legacy_key:
        ordered_keys.append(str(legacy_key))

    seen: set[str] = set()
    unique_keys: list[str] = []
    for key in ordered_keys or ["default"]:
        if key not in seen:
            unique_keys.append(key)
            seen.add(key)

    return unique_keys


def select_company_key(
    app_config: Mapping[str, Any] | None = None,
    requested_key: str | None = None,
    claims: Mapping[str, Any] | None = None,
) -> str | None:
    """Choose an allowed company key for the current request context."""

    config = app_config or getattr(current_app, "config", {})
    allowed_keys = available_company_keys(config)

    claim_key = None
    if claims:
        for candidate in (claims.get("company_key"), claims.get("company")):
            if candidate:
                claim_key = str(candidate)
                break

    if claim_key and claim_key in allowed_keys:
        return claim_key

    if requested_key and requested_key in allowed_keys:
        return requested_key

    default_key = config.get("COMPANY_KEY")
    if default_key and default_key in allowed_keys:
        return default_key

    return allowed_keys[0] if allowed_keys else None


def resolve_company_profile(
    app_config: Mapping[str, Any] | None = None, company_key: str | None = None
) -> dict[str, Any]:
    """Return the configured company profile for the given key.

    Falls back to the default company configuration when the provided key is not
    found. Legacy single-company settings (COMPANY_NAME, COMPANY_ADDRESS, etc.)
    are also respected so existing deployments remain compatible.
    """

    config = app_config or getattr(current_app, "config", {})
    profiles = _normalize_profiles(config.get("COMPANY_PROFILES"))

    legacy_profile = {
        "key": config.get("COMPANY_KEY") or "default",
        "name": config.get("COMPANY_NAME"),
        "address_lines": [],
        "contact": config.get("COMPANY_CONTACT"),
        "tagline": config.get("COMPANY_TAGLINE"),
        "logo_path": config.get("COMPANY_LOGO_PATH"),
    }
    default_profile = profiles[0] if profiles else legacy_profile

    selected_key = company_key or config.get("COMPANY_KEY") or default_profile.get("key")
    selected = next(
        (profile for profile in profiles if profile.get("key") == selected_key),
        default_profile,
    )

    return {
        "key": selected.get("key") or default_profile.get("key"),
        "name": selected.get("name") or legacy_profile.get("name"),
        "address_lines": selected.get("address_lines") or legacy_profile.get("address_lines") or [],
        "contact": selected.get("contact") or legacy_profile.get("contact"),
        "tagline": selected.get("tagline") or legacy_profile.get("tagline"),
        "logo_path": selected.get("logo_path") or legacy_profile.get("logo_path"),
    }
