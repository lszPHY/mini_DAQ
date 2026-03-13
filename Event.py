# Event.py
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

from Signal import Hit


@dataclass(frozen=True, slots=True)
class HitCluster:
    """
    One cluster within one Event.

    hit_idx: indices into Event.hits
    ledge_min/max: cluster time span in ledge units (or converted units if you prefer)
    layer/col bbox: quick geometry summary (useful for track seeding)
    """
    hit_idx: Tuple[int, ...]
    ledge_min: int
    ledge_max: int
    layer_min: int
    layer_max: int
    col_min: int
    col_max: int


@dataclass(frozen=True, slots=True)
class Event:
    event_id20: int
    rd_bank_sel: int
    trigger_count: int
    hit_count_expected: int

    hits: List[Hit]
    raw_bytes: bytes = b""
    # NEW: derived reconstruction products
    clusters: Tuple[HitCluster, ...] = ()
