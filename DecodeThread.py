# DecodeThread.py
from __future__ import annotations

import time
import queue
from dataclasses import dataclass
from typing import List, Optional
#used in tigger time determination
import os 
import csv
from bisect import bisect_left

import numpy as np
from PyQt5 import QtCore

from Signal import SignalType, Hit, decode_word5
from Event import Event
from geometry import Geometry


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

    valid_events: int
    kept_events: int
    pass_rate: float


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
        geo= None,
        dat_out_path: Optional[str]= None,
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
        self.geo=geo
        self.dat_out_path = dat_out_path
        self._fh= None
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
        #raw bytes
        self._cur_raw = bytearray()
        self._last_emit = time.time()
        #counting numbers
        self._evt_valid_total = 0
        self._evt_kept_total = 0
        self._stream_buf = bytearray()

        # trigger study
        self._word_index = 0
        self._cur_header_word_index = None
        self._all_triggers = []   # list of dict: word_index, raw40, time_ns
        self._all_events_for_trigger_study = []  # list of dict: event_id, header_word_index, hit_times_ns
        self._trigger_study_csv_written = False
        self._midrun_csv_written = False

        # trigger timestamp settings
        self._trigger_time_lsb_ns = 0.78125
        self._trigger_time_mask = 0x1FFFF   # low 17 bits
        self._trigger_time_period_ns = (self._trigger_time_mask + 1) * self._trigger_time_lsb_ns

        self._store_trigger_dt_min_ns = 196
        self._store_trigger_dt_max_ns = 596
        self._cur_first_trigger_after_header = None
        self._pending_events_for_store = []
        self._dt_debug_print_count = 0
        self._kept_match_rows = []

    def _maybe_write_midrun_csv(self):
        if self._midrun_csv_written:
            return
        if self._evt_valid_total < 100000:
            return

        # self._write_kept_match_csv()
        # self._write_trigger_study_csv()
        self._midrun_csv_written = True
    def _write_kept_match_csv(self):
        if not self._kept_match_rows:
            return

        if self.dat_out_path:
            base, ext = os.path.splitext(self.dat_out_path)
            csv_path = base + "_kept_matches.csv"
        else:
            csv_path = os.path.join(os.getcwd(), "kept_matches.csv")

        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "source",
                    "event_id20",
                    "header_word_index",
                    "trigger_word_index",
                    "trigger_time_ns",
                    "event_min_hit_time_ns",
                    "dt_ns_wrapped",
                    "n_hits",
                ]
            )
            for row in self._kept_match_rows:
                writer.writerow(
                    [
                        row["source"],
                        row["event_id20"],
                        row["header_word_index"],
                        row["trigger_word_index"],
                        row["trigger_time_ns"],
                        row["event_min_hit_time_ns"],
                        row["dt_ns_wrapped"],
                        row["n_hits"],
                    ]
                )


    def stop(self):
        self._stop = True

    def _reset_event(self):
        self._cur_open = False
        self._cur_event_id20 = 0
        self._cur_rd_bank_sel = 0
        self._cur_hits = []
        self._cur_hit_count = 0
        self._cur_header_word_index = None
        self._cur_first_trigger_after_header = None
        self._cur_raw = bytearray()

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
        raw_bytes = bytes(self._cur_raw)

        hit_times_ns = [float(h.ledge) * self._trigger_time_lsb_ns for h in hits]
        self._all_events_for_trigger_study.append(
            {
                "event_id": self._cur_event_id20,
                "header_word_index": self._cur_header_word_index,
                "hit_times_ns": hit_times_ns,
            }
        )

        self._evt_valid_total += 1
        self._maybe_write_midrun_csv()
        event_min_hit_time_ns = min(hit_times_ns) if hit_times_ns else None
        event_median_hit_time_ns = float(np.median(hit_times_ns)) if hit_times_ns else None
        event_ref_hit_time_ns = event_median_hit_time_ns
        # Case 1: the FIRST trigger after the header already appeared before trailer.
        if self._cur_first_trigger_after_header is not None:
            trigger_raw40 = int(self._cur_first_trigger_after_header)
            trigger_time_ns = float(trigger_raw40 & self._trigger_time_mask) * self._trigger_time_lsb_ns

            dt_ns = self._wrap_delta_ns(event_ref_hit_time_ns - trigger_time_ns)

            keep = self._should_store_event(
                hits,
                event_ref_hit_time_ns=event_ref_hit_time_ns,
                trigger_time_ns=trigger_time_ns,
            )
            if keep:
                self._kept_match_rows.append(
                    {
                        "source": "open",
                        "event_id20": self._cur_event_id20,
                        "header_word_index": self._cur_header_word_index,
                        "trigger_word_index": self._word_index,
                        "trigger_time_ns": trigger_time_ns,
                        "event_min_hit_time_ns": event_ref_hit_time_ns,
                        "dt_ns_wrapped": dt_ns,
                        "n_hits": len(hits),
                    }
                )
                trigger_raw_bytes = trigger_raw40.to_bytes(5, byteorder="big")
                raw_bytes_to_store = raw_bytes + trigger_raw_bytes

                ev = Event(
                    event_id20=self._cur_event_id20,
                    rd_bank_sel=self._cur_rd_bank_sel,
                    trigger_count=trigger_count,
                    hit_count_expected=hit_expected,
                    hits=hits,
                    raw_bytes=raw_bytes_to_store,
                )
                self.buf.push(ev)
                self._evt_kept_total += 1
                if self._fh is not None:
                    self._fh.write(raw_bytes_to_store)

            self._reset_event()
            return

        # Case 2: no trigger yet; defer until the FIRST later trigger arrives.
        self._pending_events_for_store.append(
            {
                "event_id20": self._cur_event_id20,
                "rd_bank_sel": self._cur_rd_bank_sel,
                "trigger_count": trigger_count,
                "hit_count_expected": hit_expected,
                "hits": hits,
                "raw_bytes": raw_bytes,
                "header_word_index": self._cur_header_word_index,
                "event_min_hit_time_ns": event_min_hit_time_ns,
                "event_median_hit_time_ns": event_median_hit_time_ns,
            }
        )
        self._reset_event()
    #determine if there are different clusters
    def _largest_cluster_size(self, fired_tubes, max_dlayer=1, max_dcol=2):
        """
        fired_tubes: set of (layer, col)
        return: size of largest connected component
        """
        points = list(fired_tubes)
        n = len(points)
        if n == 0:
            return 0

        visited = [False] * n

        def is_neighbor(p1, p2):
            l1, c1 = p1
            l2, c2 = p2
            return abs(l1 - l2) <= max_dlayer and abs(c1 - c2) <= max_dcol

        largest = 0

        for i in range(n):
            if visited[i]:
                continue

            stack = [i]
            visited[i] = True
            comp_size = 0

            while stack:
                u = stack.pop()
                comp_size += 1
                for v in range(n):
                    if not visited[v] and is_neighbor(points[u], points[v]):
                        visited[v] = True
                        stack.append(v)

            largest = max(largest, comp_size)

        return largest
    #filter
    def _should_store_event(
        self,
        hits: List[Hit],
        event_ref_hit_time_ns: Optional[float] = None,
        trigger_time_ns: Optional[float] = None,
    ) -> bool:
        # Timing cut: keep only if the matched trigger exists and
        # trigger_time - earliest_hit_time is within the configured window.
        if event_ref_hit_time_ns is None or trigger_time_ns is None:
            return False

        dt_ns = self._wrap_delta_ns(event_ref_hit_time_ns - trigger_time_ns)
        if self._dt_debug_print_count < 200:
            print(
                "dt_ns=", dt_ns,
                "event_ref_hit_time_ns=", event_ref_hit_time_ns,
                "trigger_time_ns=", trigger_time_ns,
            )
            self._dt_debug_print_count += 1
        if not (self._store_trigger_dt_min_ns <= dt_ns <= self._store_trigger_dt_max_ns):
            return False

        fired_layers = set()
        layer_to_cols = {}
        layer_to_xs = {}
        X_GAP_THRESHOLD = 30.0
        X_NEIGHBOR_THRESHOLD = 45.0
        for h in hits:
            layer = int(h.layer)
            col = int(h.col)
            x = float(h.x)
            # 没有正确几何映射，直接不存，避免误判
            if layer < 0 or col < 0:
                return False

            fired_layers.add(layer)

            if layer not in layer_to_cols:
                layer_to_cols[layer] = set()
            layer_to_cols[layer].add(col)

            if layer not in layer_to_xs:
                layer_to_xs[layer] = []
            layer_to_xs[layer].append(x)

        # 至少经过 6 个全局 layer（0~7）
        if len(fired_layers) < 6:
            return False

        # 每层最多 2 根 tube
        for cols in layer_to_cols.values():
            if len(cols) > 2:
                return False
        # 新增过滤条件：相邻两层之间，必须至少存在一对 hit，
        # 其横向间距 <= 2 个 tube；否则认为轨迹在相邻层之间跳得太远，不保留。
        sorted_layers = sorted(layer_to_xs.keys())
        for i in range(len(sorted_layers) - 1):
            l0 = sorted_layers[i]
            l1 = sorted_layers[i + 1]

            # 这里只检查真正相邻的全局层
            if l1 != l0 + 1:
                continue

            xs0 = layer_to_xs.get(l0, [])
            xs1 = layer_to_xs.get(l1, [])
            if not xs0 or not xs1:
                return False

            close_enough = False
            for x0 in xs0:
                for x1 in xs1:
                    if abs(x0 - x1) <= X_NEIGHBOR_THRESHOLD:
                        close_enough = True
                        break
                if close_enough:
                    break

            if not close_enough:
                return False

        for xs in layer_to_xs.values():
            xs = sorted(set(xs))
            if len(xs) <= 1:
                continue

            if max(xs) - min(xs) >= X_GAP_THRESHOLD:
                return False

        return True
    

    def _wrap_delta_ns(self, delta_ns: float) -> float:
        period = self._trigger_time_period_ns
        return ((delta_ns + period / 2.0) % period) - period / 2.0

    def _find_trigger_candidates(self, header_word_index: int):
        trigger_word_indices = [t["word_index"] for t in self._all_triggers]
        pos = bisect_left(trigger_word_indices, header_word_index)

        candidates = []
        if pos - 2 >= 0:
            candidates.append((-2, self._all_triggers[pos - 2]))
        if pos - 1 >= 0:
            candidates.append((-1, self._all_triggers[pos - 1]))
        if pos < len(self._all_triggers):
            candidates.append((+1, self._all_triggers[pos]))
        if pos + 1 < len(self._all_triggers):
            candidates.append((+2, self._all_triggers[pos + 1]))
        return candidates

    def _write_trigger_study_csv(self):
        print("WRITE CSV CALLED，idself=",id(self))
        print("dat_out_path =", self.dat_out_path)
        print("n_triggers =", len(self._all_triggers))
        print("n_events =", len(self._all_events_for_trigger_study))
        if self._trigger_study_csv_written:
            return
        self._trigger_study_csv_written = True
        
        if not self._all_events_for_trigger_study or not self._all_triggers:
            return

        if self.dat_out_path:
            base, ext = os.path.splitext(self.dat_out_path)
            csv_path = base + "_trigger_study.csv"
        else:
            csv_path = os.path.join(os.getcwd(), "trigger_study.csv")
        
        print("csv_path =", csv_path)

        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "event_id",
                    "header_word_index",
                    "candidate_offset",
                    "trigger_word_index",
                    "trigger_raw40_hex",
                    "trigger_time_ns",
                    "hit_index",
                    "hit_time_ns",
                    "delta_ns_wrapped",
                    "event_min_hit_time_ns",
                    "event_min_hit_delta_ns_wrapped",
                ]
            )

            for ev in self._all_events_for_trigger_study:
                header_word_index = ev["header_word_index"]
                if header_word_index is None:
                    continue

                hit_times_ns = ev["hit_times_ns"]
                if not hit_times_ns:
                    continue

                candidates = self._find_trigger_candidates(header_word_index)
                if not candidates:
                    continue

                event_min_hit_time_ns = min(hit_times_ns)

                for offset, trig in candidates:
                    trig_time_ns = trig["time_ns"]
                    trig_raw40_hex = hex(trig["raw40"])
                    event_min_hit_delta = self._wrap_delta_ns(event_min_hit_time_ns - trig_time_ns)

                    for hit_idx, hit_time_ns in enumerate(hit_times_ns):
                        delta_ns = self._wrap_delta_ns(hit_time_ns - trig_time_ns)
                        writer.writerow(
                            [
                                ev["event_id"],
                                header_word_index,
                                offset,
                                trig["word_index"],
                                trig_raw40_hex,
                                trig_time_ns,
                                hit_idx,
                                hit_time_ns,
                                delta_ns,
                                event_min_hit_time_ns,
                                event_min_hit_delta,
                            ]
                        )


    
    def run(self):
        print("DECODE RUN START, id(self) =", id(self), "dat_out_path =", self.dat_out_path)
        if self.dat_out_path:
            self._fh= open(self.dat_out_path, "wb")
        
        try:
            while not self._stop:
                try:
                    chunk = self.q.get(timeout=0.01)
                except queue.Empty:
                    self._emit_1hz_if_needed()
                    continue

                # ? NO GEO HERE
                """ print("chunk_len =", len(chunk), "remainder =", len(chunk) % 5) """
                #edited geo
                WORD_SIZE = 5
                #n = len(chunk) // WORD_SIZE
                self._stream_buf.extend(chunk)
                n = len(self._stream_buf)//WORD_SIZE
                if n ==0:
                    self._emit_1hz_if_needed()
                    continue
                #added
                for i in range(n):
                    word5 = bytes(self._stream_buf[i * WORD_SIZE:(i + 1) * WORD_SIZE])
                    s = decode_word5(word5, geo=self.geo)
                    #word5 = chunk[i * WORD_SIZE:(i + 1) * WORD_SIZE]
                    #s = decode_word5(word5, geo=self.geo)   
                    st = s.type
                    self._word_index+=1

                    if st == SignalType.EVENT_HEADER and s.header is not None: #header
                        self._hdr += 1
                        if self._cur_open:
                            self._err_missing_trailer += 1
                            self._reset_event()

                        self._cur_open = True
                        self._cur_event_id20 = int(s.header.event_id20)
                        self._cur_rd_bank_sel = int(s.header.rd_bank_sel)
                        self._cur_header_word_index = self._word_index
                        self._cur_first_trigger_after_header = None
                        self._cur_hits = []
                        self._cur_hit_count = 0
                        self._cur_raw = bytearray()
                        self._cur_raw.extend(word5)
                        continue

                    if st == SignalType.HIT and s.hit is not None: #hit
                        self._hit_total += 1
                        if self._cur_open:
                            self._cur_hits.append(s.hit)
                            self._cur_hit_count += 1
                            self._cur_raw.extend(word5)
                        continue

                    if st == SignalType.EVENT_TRAILER and s.trailer is not None: #trailer
                        self._trl += 1
                        if not self._cur_open:
                            self._err_missing_header += 1
                            continue
                        self._cur_raw.extend(word5)

                        self._finalize_event(
                            trailer_event_id20=int(s.trailer.event_id20),
                            trigger_count=int(s.trailer.trigger_count),
                            hit_expected=int(s.trailer.hit_count),
                        )
                        continue

                    if st == SignalType.TRIGGER:
                        self._trg += 1
                        trigger_raw40 = s.raw40
                        trigger_time_ns = float(trigger_raw40 & self._trigger_time_mask) * self._trigger_time_lsb_ns

                        self._all_triggers.append(
                            {
                                "word_index": self._word_index,
                                "raw40": trigger_raw40,
                                "time_ns": trigger_time_ns,
                            }
                        )

                        # Prefer the currently open event: if a trigger arrives while an event is open,
                        # treat it as the first trigger for that current event only.
                        if self._cur_open:
                            if self._cur_first_trigger_after_header is None:
                                self._cur_first_trigger_after_header = trigger_raw40
                            continue

                        # Otherwise, use this trigger for the MOST RECENT finalized event that is still
                        # waiting for its first later trigger.
                        if self._pending_events_for_store:
                            pending = self._pending_events_for_store.pop()
                            event_ref_hit_time_ns = pending.get("event_median_hit_time_ns")

                            if event_ref_hit_time_ns is not None:
                                hits = pending["hits"]
                                if self._dt_debug_print_count < 260:
                                    print(
                                        "PENDING MATCH:",
                                        "header_word_index=", pending.get("header_word_index"),
                                        "trigger_word_index=", self._word_index,
                                        "word_gap=", self._word_index - pending.get("header_word_index", self._word_index),
                                        "event_ref_hit_time_ns=", event_ref_hit_time_ns,
                                        "trigger_time_ns=", trigger_time_ns,
                                    )

                                keep = self._should_store_event(
                                    hits,
                                    event_ref_hit_time_ns=event_ref_hit_time_ns,
                                    trigger_time_ns=trigger_time_ns,
                                )

                                if keep:
                                    dt_ns = self._wrap_delta_ns(event_ref_hit_time_ns - trigger_time_ns)
                                    self._kept_match_rows.append(
                                        {
                                            "source": "pending",
                                            "event_id20": pending["event_id20"],
                                            "header_word_index": pending.get("header_word_index"),
                                            "trigger_word_index": self._word_index,
                                            "trigger_time_ns": trigger_time_ns,
                                            "event_min_hit_time_ns": event_ref_hit_time_ns,
                                            "dt_ns_wrapped": dt_ns,
                                            "n_hits": len(hits),
                                        }
                                    )
                                    trigger_raw_bytes = int(trigger_raw40).to_bytes(5, byteorder="big")
                                    raw_bytes_to_store = pending["raw_bytes"] + trigger_raw_bytes

                                    ev = Event(
                                        event_id20=pending["event_id20"],
                                        rd_bank_sel=pending["rd_bank_sel"],
                                        trigger_count=pending["trigger_count"],
                                        hit_count_expected=pending["hit_count_expected"],
                                        hits=hits,
                                        raw_bytes=raw_bytes_to_store,
                                    )
                                    self.buf.push(ev)
                                    self._evt_kept_total += 1
                                    if self._fh is not None:
                                        self._fh.write(raw_bytes_to_store)
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
                consumed=n*WORD_SIZE
                if consumed >0:
                    self._stream_buf=self._stream_buf[consumed:]

                self._emit_1hz_if_needed()
        finally:
            if self._fh is not None:
                self._fh.flush()
                self._fh.close()
                self._fh = None
        
            # self._pending_events_for_store.clear()
            # self._write_kept_match_csv()
            # self._write_trigger_study_csv()

    def _emit_1hz_if_needed(self):
        now = time.time()
        if now - self._last_emit < 1.0:
            return
        pass_rate=0.0
        if self._evt_valid_total and self._evt_valid_total >0:
            pass_rate = self._evt_kept_total / self._evt_valid_total

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
            valid_events=self._evt_valid_total,
            kept_events=self._evt_kept_total,
            pass_rate=pass_rate,
        )
        self.analysis_1hz.emit(snap)
        self._last_emit = now
