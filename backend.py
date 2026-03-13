# backend.py
# Integrates CaptureThread (.dat writing + byte queue) and DecodeThread (event build + stats)

from __future__ import annotations

import os
import time
import queue
from typing import Optional, Any, Dict, List

from PyQt5 import QtCore

from CaptureThread import CaptureThread
from DecodeThread import DecodeThread, EventBuffer

from DatReplayThread import DatReplayThread
#replay data from stored dat file
def _timestamp_yyyymmdd_hhmmss() -> str:
    return time.strftime("%Y%m%d_%H%M%S", time.localtime())


class Backend(QtCore.QObject):
    """
    Backend wires:
      CaptureThread -> analysis_q -> DecodeThread

    Exposes:
      - message(str): capture messages
      - stats(total_packets, lost_total, total_bytes, filename): capture stats
      - analysis_1hz(DecodeSnapshot): decode stats + histograms (valid events only)

    Provides:
      - pop_event() to retrieve valid events from EventBuffer

    Geometry registry (GUI-side, NOT used by DecodeThread):
      - set_geometries_from_list([geo0, geo1, ...])
      - get_geometry(chamber_id)
      - geometry_count()

    NEW (global event navigation):
      - event_changed(Event|None): emitted whenever the *current* event changes
      - next_event(), prev_event(), goto_event(idx)
      - current_event(), current_index(), cache_size()
      - clear_event_cache()
    """

    message = QtCore.pyqtSignal(str)
    stats = QtCore.pyqtSignal(int, int, int, str)
    capture_started = QtCore.pyqtSignal()
    analysis_1hz = QtCore.pyqtSignal(object)  # DecodeSnapshot

    # NEW: broadcast current event to all tabs
    event_changed = QtCore.pyqtSignal(object)  # Event or None

    def __init__(self, parent=None):
        super().__init__(parent)

        # bounded queue so decode can't eat RAM
        self._analysis_q: "queue.Queue[bytes]" = queue.Queue(maxsize=256)

        self._cap_thread: Optional[CaptureThread] = None
        self._dec_thread: Optional[DecodeThread] = None

        self._event_buf: Optional[EventBuffer] = None
        self._current_out_path: Optional[str] = None

        # chamber_id -> Geometry (GUI / mapping only)
        self._geos: Dict[int, Any] = {}

        # ---------------- NEW: global navigation state ----------------
        self._ev_cache: List[Any] = []  # cached Events in the order user browsed
        self._ev_idx: int = -1          # index into _ev_cache (-1 means none selected)

    # ---------------- geometry registry (GUI-side) ----------------

    def set_geometries_from_list(self, geos: List[Any]) -> None:
        """
        Register geometries by reading each geo.chamber_id.

        Safe behavior:
          - requires chamber_id >= 0
          - raises on duplicates instead of silently overwriting
        """
        out: Dict[int, Any] = {}
        for g in (geos or []):
            if g is None:
                continue
            cid = int(getattr(g, "chamber_id", -1))
            if cid < 0:
                raise ValueError("Geometry missing valid chamber_id (must be >= 0)")
            if cid in out:
                raise ValueError(f"Duplicate chamber_id {cid}")
            out[cid] = g
        self._geos = out

    def set_geometries(self, geos: Dict[int, Any]) -> None:
        """
        Optional alternative API: register by explicit dict.
        (Keeps compatibility if you still want to call it this way somewhere.)
        """
        out: Dict[int, Any] = {}
        for cid, g in (geos or {}).items():
            if g is None:
                continue
            out[int(cid)] = g
        self._geos = out

    def get_geometry(self, chamber_id: int) -> Optional[Any]:
        return self._geos.get(int(chamber_id))

    def geometry_count(self) -> int:
        return len(self._geos)

    def geometries(self) -> Dict[int, Any]:
        return dict(self._geos)

    # ---------------- public API ----------------

    def is_running(self) -> bool:
        return self._cap_thread is not None and self._cap_thread.isRunning()

    def make_out_path(self, out_dir: str, run: int) -> str:
        out_dir = out_dir.strip() or os.getcwd()
        ts = _timestamp_yyyymmdd_hhmmss()
        fname = f"run{run:05d}_{ts}.dat"
        return os.path.join(out_dir, fname)

    def start_capture(self, dev: str, bpf: str, out_path: str, max_events_in_ram: int = 256):
        """
        Start capture + decode.

        DecodeThread is SINGLE and does NOT use geometry.
                Added: DecodeThread uses the global chamber geometry and writes filtered events to .dat.
        """
        if self.is_running():
            self.stop_capture()

        self._current_out_path = str(out_path)

        # clear analysis queue (drop old bytes)
        try:
            while True:
                self._analysis_q.get_nowait()
        except queue.Empty:
            pass

        # NEW: reset navigation on new run
        self.clear_event_cache(emit_signal=False)

        # event buffer for valid events
        self._event_buf = EventBuffer(max_events=max_events_in_ram)
        
        geo_for_decode = None
        if self._geos:
            geo_for_decode = next(iter(self._geos.values()))

        # decode thread (consumer)
        self._dec_thread = DecodeThread(
            analysis_q=self._analysis_q,
            event_buffer=self._event_buf,
            #added geo
            geo=geo_for_decode,
            dat_out_path=out_path,

            max_tdcs=40,
            adc_bins=256,
            tdc_bins=4096,
            tdc_shift=5,
        )
        self._dec_thread.analysis_1hz.connect(self.analysis_1hz)

        # capture thread (producer)
        self._cap_thread = CaptureThread(
            dev=dev,
            bpf=bpf,
            out_path=out_path,
            analysis_q=self._analysis_q,
        )
        self._cap_thread.message.connect(self.message)
        self._cap_thread.stats.connect(self.stats)

        # start consumer first, then producer
        self._dec_thread.start()
        self.capture_started.emit()
        self._cap_thread.start()

        # Optional: broadcast "no current event" at run start
        self.event_changed.emit(None)

    def stop_capture(self):
        # stop producer first
        if self._cap_thread:
            self._cap_thread.stop()
            self._cap_thread.wait(2000)
            self._cap_thread = None

        # then stop consumer
        if self._dec_thread:
            self._dec_thread.stop()
            self._dec_thread.wait(2000)
            self._dec_thread = None

    # ---------------- legacy event access (kept) ----------------

    def pop_event(self):
        """Return next valid Event from buffer (or None)."""
        if self._event_buf is None:
            return None
        return self._event_buf.pop()

    def events_buffered(self) -> int:
        if self._event_buf is None:
            return 0
        return self._event_buf.size()

    def current_out_path(self) -> Optional[str]:
        return self._current_out_path

    # ---------------- NEW: global event navigation ----------------

    def cache_size(self) -> int:
        return len(self._ev_cache)

    def current_index(self) -> int:
        return int(self._ev_idx)

    def current_event(self) -> Optional[Any]:
        if 0 <= self._ev_idx < len(self._ev_cache):
            return self._ev_cache[self._ev_idx]
        return None

    def goto_event(self, idx: int) -> None:
        idx = int(idx)
        if not (0 <= idx < len(self._ev_cache)):
            return
        self._ev_idx = idx
        self.event_changed.emit(self._ev_cache[self._ev_idx])

    def next_event(self) -> None:
        """
        Advance by 1:
          - If next is already cached, move forward.
          - Else, pop from EventBuffer, append to cache, select it.
        """
        # cached next
        if 0 <= self._ev_idx < len(self._ev_cache) - 1:
            self._ev_idx += 1
            self.event_changed.emit(self._ev_cache[self._ev_idx])
            return

        # need a fresh event
        ev = self.pop_event()
        if ev is None:
            # no new event; re-emit current (so UI can refresh buffered count)
            self.event_changed.emit(self.current_event())
            return

        self._ev_cache.append(ev)
        self._ev_idx = len(self._ev_cache) - 1
        self.event_changed.emit(ev)

    def prev_event(self) -> None:
        """
        Go back by 1 within cache (cannot go back into EventBuffer).
        """
        if self._ev_idx <= 0:
            self.event_changed.emit(self.current_event())
            return
        self._ev_idx -= 1
        self.event_changed.emit(self._ev_cache[self._ev_idx])

    def clear_event_cache(self, emit_signal: bool = True, clear_buffer: bool = False):
        self._ev_cache.clear()
        self._ev_idx = -1

        if clear_buffer and self._event_buf is not None:
            self._event_buf.clear()

        if emit_signal:
            self.event_changed.emit(None)
    
    def start_replay_dat(self, dat_path: str, out_filtered_path: Optional[str]=None, max_events_in_ram: int = 256, max_mb: int = 0, realtime: bool = False):
        """
        Offline replay: read a saved .dat file and feed DecodeThread,
        so GUI plots update using the EXISTING pyqtgraph logic.
        """
        self._current_out_path= str(out_filtered_path) if out_filtered_path else None
        if self.is_running():
            self.stop_capture()

        # clear analysis queue
        try:
            while True:
                self._analysis_q.get_nowait()
        except queue.Empty:
            pass

        # reset navigation cache
        self.clear_event_cache(emit_signal=False)

        # event buffer for valid events
        self._event_buf = EventBuffer(max_events=max_events_in_ram)
        geo_for_decode=None
        if self._geos:
            geo_for_decode=next(iter(self._geos.values()))
        # decode thread (consumer)
        self._dec_thread = DecodeThread(
            analysis_q=self._analysis_q,
            event_buffer=self._event_buf,
            dat_out_path= out_filtered_path,
            geo=geo_for_decode,
            max_tdcs=40,
            adc_bins=256,
            tdc_bins=4096,
            tdc_shift=5,
        )
        self._dec_thread.analysis_1hz.connect(self.analysis_1hz)

        # replay thread (producer) -- reuse _cap_thread slot to keep stop_capture() working
        self._cap_thread = DatReplayThread(
            dat_path=dat_path,
            analysis_q=self._analysis_q,
            max_mb=max_mb,
            realtime=realtime,
        )
        self._cap_thread.message.connect(self.message)
        self._cap_thread.stats.connect(self.stats)

        self._dec_thread.start()
        self.capture_started.emit()
        self._cap_thread.start()

        self.event_changed.emit(None)
