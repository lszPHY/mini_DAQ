# tab_geometry.py
from __future__ import annotations

import os
from typing import List, Tuple, Dict, Any

from PyQt5 import QtWidgets, QtCore
import pyqtgraph as pg

pg.setConfigOption("background", "w")
pg.setConfigOption("foreground", "k")

from geometry import Geometry
from geometry_dialog import GeometryConfigDialog


# =============================================================================
# Drawing item
# =============================================================================

class _TubeItem(QtWidgets.QGraphicsEllipseItem):
    __slots__ = ("layer", "col", "tdc_slot_idx")

    def __init__(self, x_mm: float, y_mm: float, r_mm: float):
        super().__init__(x_mm - r_mm, y_mm - r_mm, 2 * r_mm, 2 * r_mm)
        self.setPen(pg.mkPen(color=(80, 80, 80), width=1.0))
        self.setBrush(pg.mkBrush(255, 255, 255))
        self.layer = -1
        self.col = -1
        self.tdc_slot_idx = -1


# =============================================================================
# Main tab
# =============================================================================

class tab_geometry(QtCore.QObject):
    def __init__(
        self,
        parent_widget,
        geo: Geometry,
        backend,
        chamber_id: int,
        n_tdcs: int = 40,
        default_filename: str = "geometry.txt",
    ):
        super().__init__(parent_widget)
        self.parent = parent_widget
        self.backend = backend
        self.geo = geo
        self.chamber_id = int(chamber_id)
        self.default_filename = str(default_filename)

        # Use Geometry's loaded assignment if available; otherwise fall back to defaults.
        self.slots_per_ml = int(getattr(self.geo, "slots_per_ml", 10))

        if getattr(self.geo, "ml0", None) and getattr(self.geo, "ml1", None):
            self.ml0_slots = list(self.geo.ml0[: self.slots_per_ml])
            self.ml1_slots = list(self.geo.ml1[: self.slots_per_ml])
        else:
            self.ml0_slots = [(i, 6) for i in range(self.slots_per_ml)]
            self.ml1_slots = [(10 + i, 6) for i in range(self.slots_per_ml)]

        print(
            f"[tab_geometry init] chamber={getattr(self.geo,'chamber_id',-1)} "
            f"slots_per_ml={self.slots_per_ml} ml0_slots={self.ml0_slots} ml1_slots={self.ml1_slots}"
        )

        # lookup + highlight
        self._tube_items: List[_TubeItem] = []
        self._tube_by_lc: Dict[Tuple[int, int], _TubeItem] = {}
        self._highlighted: set[Tuple[int, int]] = set()

        # label caches
        self._tdc_text_items: List[pg.TextItem] = []
        self._ch_text_items: List[pg.TextItem] = []
        self._show_channel_ids = False

        # packed starts per ML (computed in _sync_geo_tdc_map)
        self._ml_slot_starts: Dict[int, List[int]] = {0: [], 1: []}
        self._ml_slot_coverage: Dict[int, Dict[int, int]] = {0: {}, 1: {}}
        self._ml_slots_expanded: Dict[int, List[Tuple[int, int, int, int]]] = {0: [], 1: []}

        self._startup_load_error = ""

        self._sync_geo_tdc_map()
        self._build_ui()
        self._redraw()

        # global nav (backend-owned)
        if self.backend is not None and hasattr(self.backend, "event_changed"):
            self.backend.event_changed.connect(self._on_global_event_changed)

        if self.backend is not None and hasattr(self.backend, "analysis_1hz"):
            self.backend.analysis_1hz.connect(self._on_decode_tick)


        # initial sync to current backend event (if any)
        if self.backend is not None and hasattr(self.backend, "current_event"):
            ev0 = self.backend.current_event()
            if ev0 is not None:
                self.highlight_event_green(ev0)
            self._update_event_nav_ui_global(ev0)
        else:
            self._update_event_nav_ui_global(None)

    # =============================================================================
    # Debug
    # =============================================================================

    def _debug_print_active_tdcs(self, tag: str = ""):
        active = [i for i, a in enumerate(self.geo.isActiveTDC) if int(a) == 1]
        ml0 = [t for t in active if int(self.geo.TDC_ML[t]) == 0]
        ml1 = [t for t in active if int(self.geo.TDC_ML[t]) == 1]

        prefix = f"[tab_geometry]{' ' + tag if tag else ''}"
        print(f"{prefix} ActiveTDC count={len(active)} active={active}")
        print(f"{prefix}   ML0 active={ml0}")
        print(f"{prefix}   ML1 active={ml1}")
        for t in active[:10]:
            print(f"{prefix}   TDC {t:02d}: ML={int(self.geo.TDC_ML[t])} COLSTART={int(self.geo.TDC_COL[t])}")

    # =============================================================================
    # Packed-slot helpers
    # =============================================================================

    @staticmethod
    def _slot_starts(slots: List[Tuple[int, int]]) -> List[int]:
        """Packed col_start per slot index via prefix-sum of ncol."""
        starts: List[int] = []
        acc = 0
        for _, ncol in slots:
            starts.append(acc)
            acc += max(0, int(ncol))
        return starts

    def _slot_start(self, ml_id: int, slot_idx: int) -> int:
        starts = self._ml_slot_starts.get(int(ml_id), [])
        if 0 <= int(slot_idx) < len(starts):
            return int(starts[int(slot_idx)])
        return 0

    # =============================================================================
    # Mapping
    # =============================================================================

    def _sync_geo_tdc_map(self):
        # compute packed starts
        self._ml_slot_starts = {
            0: self._slot_starts(self.ml0_slots),
            1: self._slot_starts(self.ml1_slots),
        }

        active: List[int] = []
        ml: List[int] = []
        colstart: List[int] = []

        # feed Geometry with per-TDC starting column = packed start of its slot
        for ml_id, slots in [(0, self.ml0_slots), (1, self.ml1_slots)]:
            starts = self._ml_slot_starts[ml_id]
            for slot_idx, (tdc_id, ncol) in enumerate(slots):
                tdc_id = int(tdc_id)
                ncol = max(0, int(ncol))
                if tdc_id >= 0 and ncol > 0:
                    active.append(tdc_id)
                    ml.append(int(ml_id))
                    colstart.append(int(starts[slot_idx]))

        # configure geometry's tdc map
        self.geo.configure_tdc_map(active, ml, colstart, strict_duplicates=False)

        # coverage + labels cache
        self._ml_slot_coverage = {0: {}, 1: {}}
        self._ml_slots_expanded = {0: [], 1: []}

        for ml_id, slots in [(0, self.ml0_slots), (1, self.ml1_slots)]:
            starts = self._ml_slot_starts[ml_id]
            for slot_idx, (tdc_id, ncol) in enumerate(slots):
                tdc_id = int(tdc_id)
                ncol = max(0, int(ncol))
                cs = int(starts[slot_idx])

                self._ml_slots_expanded[ml_id].append((slot_idx, tdc_id, cs, ncol))

                if tdc_id < 0 or ncol <= 0:
                    continue

                for c in range(cs, cs + ncol):
                    if 0 <= c < int(self.geo.MAX_TUBE_COLUMN):
                        self._ml_slot_coverage[ml_id].setdefault(c, slot_idx)

        self._debug_print_active_tdcs("sync_geo_tdc_map")

    # =============================================================================
    # UI
    # =============================================================================

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self.parent)

        top = QtWidgets.QHBoxLayout()
        layout.addLayout(top)

        self.btn_config = QtWidgets.QPushButton("Geometry + Slots...")
        self.btn_config.clicked.connect(self._open_geometry_config_dialog)
        top.addWidget(self.btn_config)

        self.btn_prev_ev = QtWidgets.QPushButton("Prev event")
        self.btn_next_ev = QtWidgets.QPushButton("Next event")
        top.addWidget(self.btn_prev_ev)
        top.addWidget(self.btn_next_ev)
        self.btn_prev_ev.clicked.connect(self._on_prev_event)
        self.btn_next_ev.clicked.connect(self._on_next_event)

        self.cb_show_ch = QtWidgets.QCheckBox("Show channel ID in tubes")
        self.cb_show_ch.setChecked(False)
        self.cb_show_ch.stateChanged.connect(self._on_toggle_channel_ids)
        top.addWidget(self.cb_show_ch)

        self.lab_status = QtWidgets.QLabel("")
        top.addWidget(self.lab_status, 1)

        self.lab_event = QtWidgets.QLabel("")
        top.addWidget(self.lab_event, 1)

        self.view = pg.PlotWidget()
        self.view.showGrid(x=True, y=True, alpha=0.2)
        self.view.setLabel("bottom", "x", units="mm")
        self.view.setLabel("left", "y", units="mm")
        self.view.setAspectLocked(True, ratio=1.0)
        self.view.setMenuEnabled(False)
        layout.addWidget(self.view, 1)

    def _on_toggle_channel_ids(self, state: int):
        self._show_channel_ids = bool(state)
        self._update_channel_labels()

    def _open_geometry_config_dialog(self):
        dlg = GeometryConfigDialog(
            self.parent,
            geo=self.geo,
            slots_per_ml=self.slots_per_ml,
            ml0_slots=self.ml0_slots,
            ml1_slots=self.ml1_slots,
            default_path=self.default_filename,
        )
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return

        # apply dialog results
        self.geo = dlg.result_geometry()
        self.slots_per_ml = dlg.result_slots_per_ml()
        self.ml0_slots = dlg.result_ml0_slots()
        self.ml1_slots = dlg.result_ml1_slots()

        self._sync_geo_tdc_map()

        # geometry changed -> clear global event browsing (events were mapped with old config)
        if self.backend is not None and hasattr(self.backend, "clear_event_cache"):
            self.backend.clear_event_cache()
        else:
            self.clear_hit_highlight()

        self._redraw()

        # (Your backend registry is set_geometries/set_geometries_from_list; no set_geometry() in backend.py.)
        # If you want to update the registry here, do it at the GUI controller level where all chambers are known.

    # =============================================================================
    # Drawing helpers
    # =============================================================================

    def _tube_brush_for_slot(self, slot_idx: int):
        if slot_idx < 0:
            return pg.mkBrush(255, 255, 255)
        return pg.mkBrush(255, 255, 255) if (slot_idx % 2 == 0) else pg.mkBrush(220, 220, 220)

    def _slot_for_tube(self, layer: int, col: int) -> int:
        ml = self.geo.multilayer_from_layer(layer)
        return self._ml_slot_coverage.get(ml, {}).get(col, -1)

    def _clear_scene(self):
        for it in self._tube_items:
            self.view.removeItem(it)
        self._tube_items.clear()

        for t in self._tdc_text_items:
            self.view.removeItem(t)
        self._tdc_text_items.clear()

        for t in self._ch_text_items:
            self.view.removeItem(t)
        self._ch_text_items.clear()

    def _add_tdc_labels(self):
        y0_top = self.geo.get_hit_xy(3, 0)[1]
        y1_top = self.geo.get_hit_xy(7, 0)[1]
        y0 = y0_top + 2.2 * self.geo.radius
        y1 = y1_top + 2.2 * self.geo.radius

        for ml_id, ylab in [(0, y0), (1, y1)]:
            for slot_idx, tdc_id, col_start, ncol in self._ml_slots_expanded[ml_id]:
                if int(tdc_id) < 0 or int(ncol) <= 0:
                    continue
                cols = [
                    c for c in range(int(col_start), int(col_start) + int(ncol))
                    if 0 <= c < int(self.geo.MAX_TUBE_COLUMN)
                ]
                if not cols:
                    continue
                rep_layer = ml_id * int(self.geo.MAX_TDC_LAYER)
                xs: List[float] = []
                for c in cols:
                    x, _ = self.geo.get_hit_xy(rep_layer, c)
                    if x >= 0:
                        xs.append(x)
                if not xs:
                    continue
                xlab = float(sum(xs) / len(xs))
                t = pg.TextItem(text=f"TDC {tdc_id:02d}", color=(0, 0, 0), anchor=(0.5, 0.0))
                t.setPos(xlab, ylab)
                self.view.addItem(t)
                self._tdc_text_items.append(t)

    def _update_channel_labels(self):
        for t in self._ch_text_items:
            self.view.removeItem(t)
        self._ch_text_items.clear()

        if not self._show_channel_ids:
            return

        for item in self._tube_items:
            layer = item.layer
            col = item.col
            ml = self.geo.multilayer_from_layer(layer)
            slot_idx = self._slot_for_tube(layer, col)
            if slot_idx < 0:
                continue

            # slot info
            tdc_id, ncol = (self.ml0_slots[slot_idx] if ml == 0 else self.ml1_slots[slot_idx])
            tdc_id = int(tdc_id)
            ncol = max(0, int(ncol))
            if tdc_id < 0 or ncol <= 0:
                continue

            col_start = self._slot_start(ml, slot_idx)
            local_col = int(col) - int(col_start)
            local_layer = int(layer) - int(ml) * int(self.geo.MAX_TDC_LAYER)

            if not (0 <= local_col < ncol and 0 <= local_layer <= int(self.geo.MAX_TDC_LAYER) - 1):
                continue

            ch = self.geo.channel_id_from_local(local_layer, local_col)
            if ch < 0:
                continue

            x, y = self.geo.get_hit_xy(layer, col)
            txt = pg.TextItem(text=f"{ch}", color=(0, 0, 0), anchor=(0.5, 0.5))
            txt.setPos(x, y)
            self.view.addItem(txt)
            self._ch_text_items.append(txt)

    def _redraw(self):
        self._clear_scene()
        self._tube_by_lc.clear()
        self._highlighted.clear()

        n_draw = 0
        xs: List[float] = []
        ys: List[float] = []

        for layer in range(int(self.geo.MAX_TUBE_LAYER)):
            for col in range(int(self.geo.MAX_TUBE_COLUMN)):
                x, y = self.geo.get_hit_xy(layer, col)
                if x < 0:
                    continue

                slot_idx = self._slot_for_tube(layer, col)
                if slot_idx < 0:
                    continue

                it = _TubeItem(x, y, float(self.geo.radius))
                it.layer = layer
                it.col = col
                it.tdc_slot_idx = slot_idx
                it.setBrush(self._tube_brush_for_slot(slot_idx))

                self.view.addItem(it)
                self._tube_items.append(it)
                self._tube_by_lc[(layer, col)] = it

                n_draw += 1
                xs.append(x)
                ys.append(y)

        if xs and ys:
            xmin, xmax = min(xs) - 2 * self.geo.radius, max(xs) + 2 * self.geo.radius
            ymin, ymax = min(ys) - 2 * self.geo.radius, max(ys) + 4 * self.geo.radius
            self.view.setXRange(xmin, xmax, padding=0.0)
            self.view.setYRange(ymin, ymax, padding=0.0)

        self._add_tdc_labels()
        self._update_channel_labels()

        tot0 = sum(max(0, int(n)) for _, n in self.ml0_slots[: self.slots_per_ml])
        tot1 = sum(max(0, int(n)) for _, n in self.ml1_slots[: self.slots_per_ml])
        self.lab_status.setText(
            f"Draw tubes: {n_draw} | slots_per_ml={self.slots_per_ml} | "
            f"MAX_TDC={self.geo.MAX_TDC} MAX_TUBE_COLUMN={self.geo.MAX_TUBE_COLUMN} | "
            f"ML0 sum(ncol)={tot0} ML1 sum(ncol)={tot1}"
        )

    # =============================================================================
    # Hit highlight + global event browsing
    # =============================================================================

    def clear_hit_highlight(self):
        for (L, C) in list(self._highlighted):
            it = self._tube_by_lc.get((L, C))
            if it is not None:
                it.setBrush(self._tube_brush_for_slot(it.tdc_slot_idx))
        self._highlighted.clear()

    def highlight_event_green(self, ev: Any):
        if ev is None:
            return
        self.clear_hit_highlight()
        green = pg.mkBrush(0, 255, 0)

        for h in getattr(ev, "hits", []):
            try:
                tdc_id = int(getattr(h, "tdcid"))
                ch_id = int(getattr(h, "ch"))
            except Exception:
                continue

            # Map hit into this geometry (if belongs)
            try:
                L, C = self.geo.get_hit_layer_column(tdc_id, ch_id)
            except Exception:
                continue

            it = self._tube_by_lc.get((L, C))
            if it is None:
                continue
            it.setBrush(green)
            self._highlighted.add((L, C))

    def _update_event_nav_ui_global(self, ev: Any = None):
        if self.backend is None:
            self.btn_prev_ev.setEnabled(False)
            self.btn_next_ev.setEnabled(False)
            self.lab_event.setText("No backend")
            return

        idx = self.backend.current_index() if hasattr(self.backend, "current_index") else -1
        n = self.backend.cache_size() if hasattr(self.backend, "cache_size") else 0
        buffered = self.backend.events_buffered() if hasattr(self.backend, "events_buffered") else 0

        has_prev = idx > 0
        has_next_cached = (0 <= idx < n - 1)
        has_next = has_next_cached or (buffered > 0)

        self.btn_prev_ev.setEnabled(bool(has_prev))
        self.btn_next_ev.setEnabled(bool(has_next))

        if ev is None and hasattr(self.backend, "current_event"):
            ev = self.backend.current_event()

        if ev is None or idx < 0 or n <= 0:
            self.lab_event.setText(f"No event selected | buffered={buffered}")
            return

        self.lab_event.setText(
            f"Event {idx + 1}/{n} | "
            f"eid20={getattr(ev, 'event_id20', -1)} | "
            f"hits={len(getattr(ev, 'hits', []))} | buffered={buffered}"
        )

    def _on_prev_event(self):
        if self.backend is not None and hasattr(self.backend, "prev_event"):
            self.backend.prev_event()

    def _on_next_event(self):
        if self.backend is not None and hasattr(self.backend, "next_event"):
            self.backend.next_event()

    # optional convenience
    def map_hit_to_wire(self, tdc_id: int, ch_id: int) -> Tuple[float, float, int, int]:
        x, y, layer, col = self.geo.wire_center_from_hit(int(tdc_id), int(ch_id))
        return x, y, layer, col

    @QtCore.pyqtSlot(object)
    def _on_global_event_changed(self, ev):
        if ev is None:
            self.clear_hit_highlight()
        else:
            self.highlight_event_green(ev)
        self._update_event_nav_ui_global(ev)

    @QtCore.pyqtSlot(object)
    def _on_decode_tick(self, snap):
        # Called ~1 Hz while decoding; refresh button enable state
        self._update_event_nav_ui_global(None)
