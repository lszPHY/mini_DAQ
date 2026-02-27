# Signal.py
# MiniDAQ signal definitions + decoder
# Hit includes spatial info; Overflow / DecodeError include tdcid

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Optional, Iterator, Protocol, Tuple

WORD_SIZE = 5


class SignalType(IntEnum):
    HIT = 1
    TRIGGER = 2
    EVENT_HEADER = 3
    EVENT_TRAILER = 4
    OVERFLOW = 5
    DECODE_ERROR = 6
    UNKNOWN = 99


# ---- geometry hook ----

class GeometryLike(Protocol):
    def wire_center_from_hit(
        self, tdc_id: int, channel_id: int
    ) -> Tuple[float, float, int, int]:
        ...


# ---------------- payloads ----------------

@dataclass(frozen=True, slots=True)
class Hit:
    csmid: int
    tdcid: int
    ch: int
    mode: int
    ledge: int
    width: int

    x: float
    y: float
    layer: int
    col: int


@dataclass(frozen=True, slots=True)
class EventHeader:
    event_id20: int
    rd_bank_sel: int


@dataclass(frozen=True, slots=True)
class EventTrailer:
    event_id20: int
    trigger_count: int
    hit_count: int


@dataclass(frozen=True, slots=True)
class Overflow:
    tdcid: int
    raw: int


@dataclass(frozen=True, slots=True)
class DecodeError:
    tdcid: int
    raw: int


# ---------------- Signal ----------------

@dataclass(frozen=True, slots=True)
class Signal:
    type: SignalType
    raw40: int

    hit: Optional[Hit] = None
    header: Optional[EventHeader] = None
    trailer: Optional[EventTrailer] = None
    overflow: Optional[Overflow] = None
    error: Optional[DecodeError] = None


# ---------------- decoder ----------------

_HDR = 0xA
_TRL = 0xC
_TRG = 0xE
_OVF_MAGIC = 0xE8
_ERR_MAGIC = 0xF7411111


def _decode_tdcid(w: int) -> int:
    """Shared TDC ID extraction."""
    csmid = (w >> 37) & 0x7
    tdc_local = (w >> 32) & 0x1F
    return tdc_local + csmid * 20


def decode_word5(word5: bytes, geo: Optional[GeometryLike] = None) -> Signal:
    if len(word5) != WORD_SIZE:
        raise ValueError("invalid word size")

    w = int.from_bytes(word5, "big")
    top4 = (w >> 36) & 0xF

    # ---------- Event header ----------
    if top4 == _HDR:
        return Signal(
            SignalType.EVENT_HEADER,
            w,
            header=EventHeader(
                event_id20=(w >> 16) & 0xFFFFF,
                rd_bank_sel=(w >> 15) & 0x1,
            ),
        )

    # ---------- Event trailer ----------
    if top4 == _TRL:
        return Signal(
            SignalType.EVENT_TRAILER,
            w,
            trailer=EventTrailer(
                event_id20=(w >> 16) & 0xFFFFF,
                trigger_count=(w >> 10) & 0x3F,
                hit_count=w & 0x3FF,
            ),
        )

    # ---------- Trigger ----------
    if top4 == _TRG and ((w >> 30) & 0x3F) == 0:
        return Signal(SignalType.TRIGGER, w)

    low32 = w & 0xFFFFFFFF

    # ---------- Decode error ----------
    if low32 == _ERR_MAGIC:
        tdcid = _decode_tdcid(w)
        return Signal(
            SignalType.DECODE_ERROR,
            w,
            error=DecodeError(tdcid=tdcid, raw=w),
        )

    # ---------- Overflow ----------
    if ((w >> 24) & 0xFF) == _OVF_MAGIC:
        tdcid = _decode_tdcid(w)
        return Signal(
            SignalType.OVERFLOW,
            w,
            overflow=Overflow(tdcid=tdcid, raw=w),
        )

    # ---------- Hit ----------
    csmid = (w >> 37) & 0x7
    tdcid = _decode_tdcid(w)
    ch = (w >> 27) & 0x1F

    x = -1.0
    y = -1.0
    layer = -1
    col = -1
    if geo is not None:
        try:
            x, y, layer, col = geo.wire_center_from_hit(tdcid, ch)
        except Exception:
            pass

    return Signal(
        SignalType.HIT,
        w,
        hit=Hit(
            csmid=csmid,
            tdcid=tdcid,
            ch=ch,
            mode=(w >> 25) & 0x3,
            ledge=(w >> 8) & 0x1FFFF,
            width=w & 0xFF,
            x=float(x),
            y=float(y),
            layer=int(layer),
            col=int(col),
        ),
    )


def decode_stream(buf: bytes, geo: Optional[GeometryLike] = None) -> Iterator[Signal]:
    n = len(buf) // WORD_SIZE
    for i in range(n):
        yield decode_word5(
            buf[i * WORD_SIZE : (i + 1) * WORD_SIZE],
            geo=geo,
        )
