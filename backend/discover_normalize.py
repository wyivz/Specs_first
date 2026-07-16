"""Deprecated shim — import from ``collectors.discovery`` instead."""

from collectors.discovery import (
    discover_skus_from_evidence,
    merge_discovery_candidates,
    sku_identity_key,
    usable_discovered_sku,
)

__all__ = [
    "discover_skus_from_evidence",
    "merge_discovery_candidates",
    "usable_discovered_sku",
    "sku_identity_key",
]
