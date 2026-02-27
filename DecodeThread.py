# DecodeThread.py
from __future__ import annotations

import time
import queue
from dataclasses import dataclass
from typing import List, Optional

import numpy as np
from PyQt5 import QtCore

from Signal import SignalType, Hit, decode_stream
from Event import Event


@dataclass(frozen=True, slots=True)
class DecodeSnapshot:
    adc_hist: np.ndarray
    tdc_hist: np.ndarray

    adc_ch_hist: np.ndarray
    tdc_ch_hist: np.ndarray

    adc_bins: int
    tdc_bins: int
    ch_adc_bins: int
    ch_tdc_bins: int

    headers: int
    trailers: int
    triggers: int
    hits_total: int

    overflow_cnt: np.ndarray
    decode_err_cnt: np.ndarray

    err_event_id: int
    err_hit_count: int
    err_missing_trailer: int
    err_missing_header: int

    events_buffered: int


class EventBuffer:
    def __init__(self, max_events: int = 256):
        self._q: "queue.Queue[Event]" = queue.Queue(maxsize=max_events)
        self.dropped = 0

    def push(self, ev: Event):
        try:
            self._q.put_nowait(ev)
        except queue.Full:
            self.dropped += 1
            return

    def pop(self) -> Optional[Event]:
        try:
            return self._q.get_nowait()
        except queue.Empty:
            return None

    def size(self) -> int:
        return self._q.qsize()

    def clear(self):
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass



class DecodeThread(QtCore.QThread):
    analysis_1hz = QtCore.pyqtSignal(object)

    def __init__(
        self,
        analysis_q: "queue.Queue[bytes]",
        event_buffer: EventBuffer,
        max_tdcs: int = 40,
        max_channels: int = 24,
        adc_bins: int = 256,
        tdc_bins: int = 4096,
        tdc_shift: int = 5,
        ch_tdc_bins: int = 1024,
        ch_tdc_shift: int = 7,
        parent: Optional[QtCore.QObject] = None,
    ):
        super().__init__(parent)

        self.q = analysis_q
        self.buf = event_buffer
        self._stop = False

        self.max_tdcs = int(max_tdcs)
        self.max_channels = int(max_channels)

        self.adc_bins = int(adc_bins)
        self.tdc_bins = int(tdc_bins)
        self.tdc_shift = int(tdc_shift)

        self.ch_adc_bins = int(adc_bins)
        self.ch_tdc_bins = int(ch_tdc_bins)
        self.ch_tdc_shift = int(ch_tdc_shift)

        # histograms (VALID EVENTS ONLY)
        self._adc = np.zeros((self.max_tdcs, self.adc_bins), dtype=np.uint32)
        self._tdc = np.zeros((self.max_tdcs, self.tdc_bins), dtype=np.uint32)

        self._adc_ch = np.zeros((self.max_tdcs, self.max_channels, self.ch_adc_bins), dtype=np.uint32)
        self._tdc_ch = np.zeros((self.max_tdcs, self.max_channels, self.ch_tdc_bins), dtype=np.uint32)

        self._ovf = np.zeros((self.max_tdcs,), dtype=np.uint32)
        self._derr = np.zeros((self.max_tdcs,), dtype=np.uint32)

        self._hdr = 0
        self._trl = 0
        self._trg = 0
        self._hit_total = 0

        self._err_eid = 0
        self._err_hit = 0
        self._err_missing_trailer = 0
        self._err_missing_header = 0

        # current event state
        self._cur_open = False
        self._cur_event_id20 = 0
        self._cur_rd_bank_sel = 0
        self._cur_hits: List[Hit] = []
        self._cur_hit_count = 0

        self._last_emit = time.time()

    def stop(self):
        self._stop = True

    def _reset_event(self):
        self._cur_open = False
        self._cur_event_id20 = 0
        self._cur_rd_bank_sel = 0
        self._cur_hits = []
        self._cur_hit_count = 0

    def _finalize_event(self, trailer_event_id20: int, trigger_count: int, hit_expected: int):
        if self._cur_event_id20 != trailer_event_id20:
            self._err_eid += 1
            self._reset_event()
            return

        if self._cur_hit_count != hit_expected:
            self._err_hit += 1
            self._reset_event()
            return

        # VALID: update histograms
        for h in self._cur_hits:
            t = int(h.tdcid)
            if not (0 <= t < self.max_tdcs):
                continue

            ch = int(h.ch)
            w = int(h.width)

            if 0 <= w < self.adc_bins:
                self._adc[t, w] += 1

            b = int(h.ledge) >> self.tdc_shift
            b = max(0, min(b, self.tdc_bins - 1))
            self._tdc[t, b] += 1

            if 0 <= ch < self.max_channels:
                if 0 <= w < self.ch_adc_bins:
                    self._adc_ch[t, ch, w] += 1

                bc = int(h.ledge) >> self.ch_tdc_shift
                bc = max(0, min(bc, self.ch_tdc_bins - 1))
                self._tdc_ch[t, ch, bc] += 1

        hits = self._cur_hits
        self._cur_hits = []

        ev = Event(
            event_id20=self._cur_event_id20,
            rd_bank_sel=self._cur_rd_bank_sel,
            trigger_count=trigger_count,
            hit_count_expected=hit_expected,
            hits=hits,
        )
        self.buf.push(ev)

        self._cur_open = False
        self._cur_event_id20 = 0
        self._cur_rd_bank_sel = 0
        self._cur_hit_count = 0

    def run(self):
        while not self._stop:
            try:
                chunk = self.q.get(timeout=0.01)
            except queue.Empty:
                self._emit_1hz_if_needed()
                continue

            # ? NO GEO HERE
            for s in decode_stream(chunk):
                st = s.type

                if st == SignalType.EVENT_HEADER and s.header is not None:
                    self._hdr += 1
                    if self._cur_open:
                        self._err_missing_trailer += 1
                        self._reset_event()

                    self._cur_open = True
                    self._cur_event_id20 = int(s.header.event_id20)
                    self._cur_rd_bank_sel = int(s.header.rd_bank_sel)
                    self._cur_hits = []
                    self._cur_hit_count = 0
                    continue

                if st == SignalType.HIT and s.hit is not None:
                    self._hit_total += 1
                    if self._cur_open:
                        self._cur_hits.append(s.hit)
                        self._cur_hit_count += 1
                    continue

                if st == SignalType.EVENT_TRAILER and s.trailer is not None:
                    self._trl += 1
                    if not self._cur_open:
                        self._err_missing_header += 1
                        continue

                    self._finalize_event(
                        trailer_event_id20=int(s.trailer.event_id20),
                        trigger_count=int(s.trailer.trigger_count),
                        hit_expected=int(s.trailer.hit_count),
                    )
                    continue

                if st == SignalType.TRIGGER:
                    self._trg += 1
                    continue

                if st == SignalType.OVERFLOW and s.overflow is not None:
                    t = int(s.overflow.tdcid)
                    if 0 <= t < self.max_tdcs:
                        self._ovf[t] += 1
                    continue

                if st == SignalType.DECODE_ERROR and s.error is not None:
                    t = int(s.error.tdcid)
                    if 0 <= t < self.max_tdcs:
                        self._derr[t] += 1
                    continue

            self._emit_1hz_if_needed()

    def _emit_1hz_if_needed(self):
        now = time.time()
        if now - self._last_emit < 1.0:
            return

        snap = DecodeSnapshot(
            adc_hist=self._adc,
            tdc_hist=self._tdc,
            adc_ch_hist=self._adc_ch,
            tdc_ch_hist=self._tdc_ch,
            adc_bins=self.adc_bins,
            tdc_bins=self.tdc_bins,
            ch_adc_bins=self.ch_adc_bins,
            ch_tdc_bins=self.ch_tdc_bins,
            headers=self._hdr,
            trailers=self._trl,
            triggers=self._trg,
            hits_total=self._hit_total,
            overflow_cnt=self._ovf,
            decode_err_cnt=self._derr,
            err_event_id=self._err_eid,
            err_hit_count=self._err_hit,
            err_missing_trailer=self._err_missing_trailer,
            err_missing_header=self._err_missing_header,
            events_buffered=self.buf.size(),
        )
        self.analysis_1hz.emit(snap)
        self._last_emit = now
