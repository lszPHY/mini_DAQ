# tab_spectra.py
from __future__ import annotations

from PyQt5 import QtWidgets, QtCore
import numpy as np
import pyqtgraph as pg

pg.setConfigOption("background", "w")  # white
pg.setConfigOption("foreground", "k")  # black axes, ticks, labels

ADC_LSB_NS = 0.78125 * 2


class TDCSelectDialog(QtWidgets.QDialog):
    """Popup dialog with checkboxes to select which TDCs to draw (multi-select)."""
    def __init__(self, parent=None, n_tdcs=40, checked=None):
        super().__init__(parent)
        self.setWindowTitle("Select TDCs")
        self.setModal(True)

        self._n_tdcs = int(n_tdcs)
        checked = set(checked or [])

        layout = QtWidgets.QVBoxLayout(self)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        layout.addWidget(scroll)

        inner = QtWidgets.QWidget()
        grid = QtWidgets.QGridLayout(inner)
        scroll.setWidget(inner)

        self._cbs = []
        cols = 4
        for tdc in range(self._n_tdcs):
            cb = QtWidgets.QCheckBox(f"TDC {tdc:02d}")
            cb.setChecked(tdc in checked)
            self._cbs.append(cb)
            r = tdc // cols
            c = tdc % cols
            grid.addWidget(cb, r, c)

        btn_row = QtWidgets.QHBoxLayout()
        layout.addLayout(btn_row)

        btn_all = QtWidgets.QPushButton("All")
        btn_none = QtWidgets.QPushButton("None")
        btn_ok = QtWidgets.QPushButton("OK")
        btn_cancel = QtWidgets.QPushButton("Cancel")

        btn_row.addWidget(btn_all)
        btn_row.addWidget(btn_none)
        btn_row.addStretch(1)
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_ok)

        btn_all.clicked.connect(lambda: [cb.setChecked(True) for cb in self._cbs])
        btn_none.clicked.connect(lambda: [cb.setChecked(False) for cb in self._cbs])
        btn_ok.clicked.connect(self.accept)
        btn_cancel.clicked.connect(self.reject)

        self.resize(420, 520)

    def selected_tdcs(self):
        return [i for i, cb in enumerate(self._cbs) if cb.isChecked()]


class _HistPlot:
    """
    Fast step-hist plot; overlay stats positioned by view ratio (not data coords).
    """
    __slots__ = ("plot", "curve", "stats_text", "_fx", "_fy")

    def __init__(self, plot: pg.PlotWidget, *, stats_pos=(0.0, 0.8), stats_anchor=(0, 1)):
        self.plot = plot
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        self.plot.setMenuEnabled(False)
        self.plot.setMouseEnabled(x=False, y=False)
        self.plot.enableAutoRange(x=False, y=True)

        self.curve = self.plot.plot(
            [], [],
            stepMode=True,
            pen=pg.mkPen(color=(0, 0, 255), width=1.5)
        )

        self._fx, self._fy = float(stats_pos[0]), float(stats_pos[1])
        self.stats_text = pg.TextItem("", anchor=stats_anchor, color=(0, 0, 0))

        vb = self.plot.getViewBox()
        vb.addItem(self.stats_text, ignoreBounds=True)
        vb.sigRangeChanged.connect(lambda *args: self._reposition_stats())

        self._reposition_stats()

    def clear(self, title=""):
        self.curve.setData([], [])
        self.plot.setTitle(title)
        self.stats_text.setText("")
        self._reposition_stats()

    def _reposition_stats(self):
        try:
            vb = self.plot.getViewBox()
            (x0, x1), (y0, y1) = vb.viewRange()
            x = x0 + self._fx * (x1 - x0)
            y = y0 + self._fy * (y1 - y0)
            self.stats_text.setPos(x, y)
        except Exception:
            return

    def update_counts(
        self,
        counts,
        *,
        title: str,
        xmin: float,
        xmax: float,
        xscale: float = 1.0,
        xlabel: str = "",
        xunits: str = "",
        show_stats: bool = True,
    ):
        counts = np.asarray(counts)
        nb = int(counts.size)
        edges = np.linspace(xmin, xmax, nb + 1, dtype=np.float64) * float(xscale)

        self.curve.setData(edges, counts, stepMode=True)
        self.plot.setTitle(title)
        self.plot.setXRange(edges[0], edges[-1], padding=0.0)

        if xlabel:
            self.plot.setLabel("bottom", xlabel, units=xunits)

        if show_stats:
            n = float(np.sum(counts))
            if n > 0:
                centers = 0.5 * (edges[:-1] + edges[1:])
                mean = float(np.dot(centers, counts) / n)
                self.stats_text.setText(f"N={int(n)}\nmean={mean:.2f} {xunits}".strip())
            else:
                self.stats_text.setText("N=0\nmean=0")

        self._reposition_stats()


class _BarPlot24:
    """
    24-bin channel occupancy plot for one TDC.
    """
    __slots__ = ("plot", "bars", "stats_text", "_fx", "_fy")

    def __init__(self, plot: pg.PlotWidget, *, stats_pos=(0.0, 0.8), stats_anchor=(0, 1)):
        self.plot = plot
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        self.plot.setMenuEnabled(False)
        self.plot.setMouseEnabled(x=False, y=False)
        self.plot.enableAutoRange(x=False, y=True)

        self._fx, self._fy = float(stats_pos[0]), float(stats_pos[1])

        x = np.arange(24, dtype=np.float64)
        self.bars = pg.BarGraphItem(
            x=x,
            height=np.zeros(24, dtype=np.float64),
            width=0.9,
            brush=pg.mkBrush(0, 0, 255)
        )
        self.plot.addItem(self.bars)

        self.plot.setXRange(-0.5, 23.5, padding=0.0)
        self.plot.setLabel("bottom", "Channel", units="")
        self.plot.getAxis("bottom").setTicks([[(i, str(i)) for i in range(0, 24, 2)]])

        self.stats_text = pg.TextItem("", anchor=stats_anchor, color=(0, 0, 0))
        vb = self.plot.getViewBox()
        vb.addItem(self.stats_text, ignoreBounds=True)
        vb.sigRangeChanged.connect(lambda *args: self._reposition_stats())
        self._reposition_stats()

    def _reposition_stats(self):
        try:
            vb = self.plot.getViewBox()
            (x0, x1), (y0, y1) = vb.viewRange()
            x = x0 + self._fx * (x1 - x0)
            y = y0 + self._fy * (y1 - y0)
            self.stats_text.setPos(x, y)
        except Exception:
            return

    def clear(self, title=""):
        self.bars.setOpts(height=np.zeros(24, dtype=np.float64))
        self.plot.setTitle(title)
        self.stats_text.setText("")
        self._reposition_stats()

    def update_counts(self, counts24, *, title: str, **_ignored):
        h = np.asarray(counts24, dtype=np.float64)
        if h.size != 24:
            h = np.resize(h, 24)

        self.bars.setOpts(height=h)
        self.plot.setTitle(title)

        n = float(h.sum())
        if n > 0:
            ch = np.arange(24, dtype=np.float64)
            mean_ch = float((ch * h).sum() / n)
            self.stats_text.setText(f"N={int(n)}\nmean_ch={mean_ch:.2f}")
        else:
            self.stats_text.setText("N=0\nmean_ch=0")

        self._reposition_stats()


# ======================================================================================
# Base Grid
# ======================================================================================

class _GridSpectraBase(QtCore.QObject):
    def __init__(
        self,
        parent_widget,
        backend,
        *,
        n_tdcs: int = 40,
        plots_rows: int = 2,
        plots_cols: int = 4,
        tab_name: str = "Spectra",
        x_label: str = "X",
        x_units: str = "",
        x_scale: float = 1.0,
        plot_wrapper_cls=_HistPlot,
    ):
        super().__init__(parent_widget)
        self.parent = parent_widget
        self.backend = backend
        self.n_tdcs = int(n_tdcs)

        self.plots_rows = int(plots_rows)
        self.plots_cols = int(plots_cols)
        self.plots_per_page = self.plots_rows * self.plots_cols

        self.tab_name = tab_name
        self.x_label = x_label
        self.x_units = x_units
        self.x_scale = float(x_scale)

        self.page = 0
        self._last_snap = None
        self._plot_wrapper_cls = plot_wrapper_cls

        self._build_ui()

        if hasattr(self.backend, "analysis_1hz"):
            self.backend.analysis_1hz.connect(self.on_analysis_1hz)
        else:
            print(f"[WARN] backend has no 'analysis_1hz' signal; {self.tab_name} will not update.\n")

    def _build_controls(self, top_layout: QtWidgets.QHBoxLayout) -> None:
        raise NotImplementedError

    def _max_pages(self) -> int:
        items = self._all_items()
        n = len(items)
        return max(1, (n + self.plots_per_page - 1) // self.plots_per_page)

    def _all_items(self) -> list:
        raise NotImplementedError

    def _items_per_page(self) -> list:
        items = self._all_items()
        start = self.page * self.plots_per_page
        return items[start:start + self.plots_per_page]

    def _plot_for_item(self, snap, item):
        raise NotImplementedError

    def _status_left_text(self) -> str:
        return ""

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self.parent)

        top = QtWidgets.QHBoxLayout()
        layout.addLayout(top)

        self._build_controls(top)

        self.capture_status = QtWidgets.QLabel("No analysis snapshot yet.")
        top.addWidget(self.capture_status, 1)

        self.grid = pg.GraphicsLayoutWidget()
        layout.addWidget(self.grid, 1)

        self._plots = []
        for r in range(self.plots_rows):
            for c in range(self.plots_cols):
                pw = self.grid.addPlot(row=r, col=c)
                pw.setLabel("bottom", self.x_label, units=self.x_units)
                pw.setLabel("left", "Counts")
                self._plots.append(self._plot_wrapper_cls(pw, stats_pos=(0.0, 0.8), stats_anchor=(0, 1)))

        nav = QtWidgets.QHBoxLayout()
        layout.addLayout(nav)

        self.lab_status = QtWidgets.QLabel("")
        nav.addWidget(self.lab_status, 1)

        self.lab_page = QtWidgets.QLabel("Page 1/1")
        self.lab_page.setMinimumWidth(110)
        self.lab_page.setAlignment(QtCore.Qt.AlignCenter)

        style = self.parent.style()
        self.btn_prev = QtWidgets.QPushButton()
        self.btn_prev.setIcon(style.standardIcon(QtWidgets.QStyle.SP_ArrowLeft))
        self.btn_next = QtWidgets.QPushButton()
        self.btn_next.setIcon(style.standardIcon(QtWidgets.QStyle.SP_ArrowRight))

        self.btn_prev.clicked.connect(self.prev_page)
        self.btn_next.clicked.connect(self.next_page)

        nav.addWidget(self.lab_page)
        nav.addWidget(self.btn_prev)
        nav.addWidget(self.btn_next)

    def _clamp_page(self):
        mp = self._max_pages()
        self.page = max(0, min(self.page, mp - 1))
        self.lab_page.setText(f"Page {self.page + 1}/{mp}")

    def prev_page(self):
        self.page -= 1
        self._clamp_page()
        self._redraw()

    def next_page(self):
        self.page += 1
        self._clamp_page()
        self._redraw()

    @QtCore.pyqtSlot(object)
    def on_analysis_1hz(self, snap):
        self._last_snap = snap
        self._clamp_page()

        # Works with BOTH snapshot formats:
        # - old: hit_cnt/trig_cnt/header_cnt/trailer_cnt
        # - new: hits_total/triggers/headers/trailers
        try:
            hits = getattr(snap, "hits_total", getattr(snap, "hit_cnt", 0))
            trg = getattr(snap, "triggers", getattr(snap, "trig_cnt", 0))
            hdr = getattr(snap, "headers", getattr(snap, "header_cnt", 0))
            trl = getattr(snap, "trailers", getattr(snap, "trailer_cnt", 0))

            ovf_arr = getattr(snap, "overflow_cnt", None)
            err_arr = getattr(snap, "decode_err_cnt", None)
            ovf = int(np.sum(ovf_arr)) if ovf_arr is not None else 0
            err = int(np.sum(err_arr)) if err_arr is not None else 0

            self.capture_status.setText(
                f"1 Hz update | hits={int(hits)} | trig={int(trg)} | "
                f"hdr={int(hdr)} | trl={int(trl)} | ovf={ovf} | err={err}"
            )
        except Exception:
            self.capture_status.setText("1 Hz update")

        self._redraw()

    def _redraw(self):
        snap = self._last_snap
        if snap is None:
            for hp in self._plots:
                hp.clear("")
            self.capture_status.setText("No analysis snapshot yet.")
            self.lab_status.setText("")
            self._clamp_page()
            return

        self._clamp_page()
        items = self._items_per_page()

        self.lab_status.setText(self._status_left_text())

        for i, hp in enumerate(self._plots):
            if i >= len(items):
                hp.clear("")
                continue

            item = items[i]
            try:
                title, counts, nbins = self._plot_for_item(snap, item)
            except Exception as e:
                hp.clear(f"plot error: {type(e).__name__}")
                continue

            counts = np.asarray(counts)
            nbins = int(nbins) if nbins is not None else int(counts.size)

            if counts.size != nbins:
                if counts.size > nbins:
                    counts = counts[:nbins]
                else:
                    counts = np.pad(counts, (0, nbins - counts.size))

            hp.update_counts(
                counts,
                title=title,
                xmin=0.0,
                xmax=float(nbins),
                xscale=self.x_scale,
                xlabel=self.x_label,
                xunits=self.x_units,
            )


# ======================================================================================
# Subclass 1: per-TDC spectra
# ======================================================================================

class tab_spectra_base(_GridSpectraBase):
    def __init__(
        self,
        parent_widget,
        backend,
        n_tdcs: int = 40,
        *,
        tab_name: str = "Spectra",
        hist_attr: str = "adc_hist",
        bins_attr: str = "adc_bins",
        x_label: str = "ADC time",
        x_units: str = "ns",
        x_scale: float = 1.0,
        title_prefix: str = "ADC",
    ):
        self.selected_tdcs = list(range(int(n_tdcs)))
        self.hist_attr = hist_attr
        self.bins_attr = bins_attr
        self.title_prefix = title_prefix

        super().__init__(
            parent_widget,
            backend,
            n_tdcs=n_tdcs,
            plots_rows=2,
            plots_cols=4,
            tab_name=tab_name,
            x_label=x_label,
            x_units=x_units,
            x_scale=x_scale,
        )

    def _build_controls(self, top_layout: QtWidgets.QHBoxLayout) -> None:
        self.btn_select = QtWidgets.QPushButton("Select TDCs")
        self.btn_select.clicked.connect(self._open_select_dialog)
        top_layout.addWidget(self.btn_select)

    def _open_select_dialog(self):
        dlg = TDCSelectDialog(self.parent, n_tdcs=self.n_tdcs, checked=self.selected_tdcs)
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            sel = dlg.selected_tdcs() or [0]
            self.selected_tdcs = sel
            self.page = 0
            self._clamp_page()
            self._redraw()

    def _all_items(self) -> list:
        return list(self.selected_tdcs)

    def _status_left_text(self) -> str:
        start = self.page * self.plots_per_page
        end = min(start + self.plots_per_page, len(self.selected_tdcs)) - 1
        if len(self.selected_tdcs) == 0:
            return "selected=0"
        return f"selected={len(self.selected_tdcs)} | showing {start} ~ {max(start, end)}"

    def _plot_for_item(self, snap, tdc):
        hists = getattr(snap, self.hist_attr, None)
        if hists is None:
            raise RuntimeError(f"missing {self.hist_attr}")

        nbins = getattr(snap, self.bins_attr, None)
        if nbins is None:
            nbins = len(hists[0]) if len(hists) else 0
        nbins = int(nbins)

        tdc = int(tdc)
        if not (0 <= tdc < len(hists)):
            return (f"TDC {tdc:02d} (out of range)", np.zeros(nbins, dtype=np.int64), nbins)

        counts = np.asarray(hists[tdc], dtype=np.int64)

        ovf_arr = getattr(snap, "overflow_cnt", None)
        err_arr = getattr(snap, "decode_err_cnt", None)
        ovf = ovf_arr[tdc] if ovf_arr is not None and tdc < len(ovf_arr) else 0
        err = err_arr[tdc] if err_arr is not None and tdc < len(err_arr) else 0

        title = f"TDC {tdc:02d} {self.title_prefix} (all ch) | ovf={int(ovf)} err={int(err)}"
        return (title, counts, nbins)


# ======================================================================================
# Subclass 2: per-channel spectra (requires snapshot to include adc_ch_hist/tdc_ch_hist)
# ======================================================================================

class tab_channel_spectra_base(_GridSpectraBase):
    def __init__(
        self,
        parent_widget,
        backend,
        n_tdcs: int = 40,
        n_channels: int = 24,
        *,
        tab_name: str = "Channel Spectra",
        hist_attr: str = "adc_ch_hist",
        bins_attr: str = "ch_adc_bins",
        x_label: str = "ADC time",
        x_units: str = "ns",
        x_scale: float = 1.0,
        title_prefix: str = "ADC",
    ):
        self.n_channels = int(n_channels)
        self.tdc = 0

        self.hist_attr = hist_attr
        self.bins_attr = bins_attr
        self.title_prefix = title_prefix

        super().__init__(
            parent_widget,
            backend,
            n_tdcs=n_tdcs,
            plots_rows=2,
            plots_cols=4,
            tab_name=tab_name,
            x_label=x_label,
            x_units=x_units,
            x_scale=x_scale,
        )

    def _build_controls(self, top_layout: QtWidgets.QHBoxLayout) -> None:
        top_layout.addWidget(QtWidgets.QLabel("TDC:"))
        self.spin_tdc = QtWidgets.QSpinBox()
        self.spin_tdc.setRange(0, self.n_tdcs - 1)
        self.spin_tdc.setValue(self.tdc)
        self.spin_tdc.valueChanged.connect(self._on_tdc_changed)
        top_layout.addWidget(self.spin_tdc)

    def _on_tdc_changed(self, v: int):
        self.tdc = int(v)
        self.page = 0
        self._clamp_page()
        self._redraw()

    def _max_pages(self) -> int:
        n = self.n_channels
        return max(1, (n + self.plots_per_page - 1) // self.plots_per_page)

    def _all_items(self) -> list:
        return list(range(self.n_channels))

    def _status_left_text(self) -> str:
        start = self.page * self.plots_per_page
        end = min(start + self.plots_per_page, self.n_channels) - 1
        return f"TDC {self.tdc:02d} | channels {start} ~ {max(start, end)}"

    def _plot_for_item(self, snap, ch):
        ch_hists = getattr(snap, self.hist_attr, None)
        if ch_hists is None:
            # With your current DecodeSnapshot, this will happen (no channel hist in Decode.py)
            raise RuntimeError(f"missing {self.hist_attr}")

        nbins = getattr(snap, self.bins_attr, None)
        if nbins is None:
            if 0 <= self.tdc < len(ch_hists) and len(ch_hists[self.tdc]) > 0:
                nbins = len(ch_hists[self.tdc][0])
            else:
                nbins = 0
        nbins = int(nbins)

        if not (0 <= self.tdc < len(ch_hists)):
            return (f"TDC {self.tdc:02d} (out of range)", np.zeros(nbins, dtype=np.int64), nbins)

        ch = int(ch)
        if not (0 <= ch < len(ch_hists[self.tdc])):
            return (f"TDC {self.tdc:02d} CH {ch:02d} (out of range)", np.zeros(nbins, dtype=np.int64), nbins)

        counts = np.asarray(ch_hists[self.tdc][ch], dtype=np.int64)
        title = f"TDC {self.tdc:02d} CH {ch:02d} {self.title_prefix}"
        return (title, counts, nbins)


class tab_channel_hits_base(_GridSpectraBase):
    def __init__(
        self,
        parent_widget,
        backend,
        n_tdcs: int = 40,
        *,
        tab_name: str = "CH Hits",
        ch_hist_attr: str = "adc_ch_hist",
        title_prefix: str = "CH hits",
    ):
        self.selected_tdcs = list(range(int(n_tdcs)))
        self.ch_hist_attr = ch_hist_attr
        self.title_prefix = title_prefix

        super().__init__(
            parent_widget,
            backend,
            n_tdcs=n_tdcs,
            plots_rows=2,
            plots_cols=4,
            tab_name=tab_name,
            x_label="Channel",
            x_units="",
            x_scale=1.0,
            plot_wrapper_cls=_BarPlot24,
        )

    def _build_controls(self, top_layout: QtWidgets.QHBoxLayout) -> None:
        self.btn_select = QtWidgets.QPushButton("Select TDCs")
        self.btn_select.clicked.connect(self._open_select_dialog)
        top_layout.addWidget(self.btn_select)

    def _open_select_dialog(self):
        dlg = TDCSelectDialog(self.parent, n_tdcs=self.n_tdcs, checked=self.selected_tdcs)
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            sel = dlg.selected_tdcs() or [0]
            self.selected_tdcs = sel
            self.page = 0
            self._clamp_page()
            self._redraw()

    def _all_items(self) -> list:
        return list(self.selected_tdcs)

    def _status_left_text(self) -> str:
        start = self.page * self.plots_per_page
        end = min(start + self.plots_per_page, len(self.selected_tdcs)) - 1
        if len(self.selected_tdcs) == 0:
            return "selected=0"
        return f"selected={len(self.selected_tdcs)} | showing {start} ~ {max(start, end)}"

    def _plot_for_item(self, snap, tdc):
        ch_hists = getattr(snap, self.ch_hist_attr, None)
        if ch_hists is None:
            raise RuntimeError(f"missing {self.ch_hist_attr}")

        tdc = int(tdc)
        if not (0 <= tdc < len(ch_hists)):
            return (f"TDC {tdc:02d} (out of range)", np.zeros(24, dtype=np.int64), 24)

        arr = np.asarray(ch_hists[tdc])
        if arr.ndim != 2:
            return (f"TDC {tdc:02d} bad dim", np.zeros(24, dtype=np.int64), 24)

        counts24 = arr.sum(axis=1)

        ovf_arr = getattr(snap, "overflow_cnt", None)
        err_arr = getattr(snap, "decode_err_cnt", None)
        ovf = ovf_arr[tdc] if ovf_arr is not None and tdc < len(ovf_arr) else 0
        err = err_arr[tdc] if err_arr is not None and tdc < len(err_arr) else 0

        title = f"TDC {tdc:02d} {self.title_prefix} | ovf={int(ovf)} err={int(err)}"
        return (title, counts24, 24)


# ======================================================================================
# Concrete Tabs
# ======================================================================================

class tab_adc_spectra(tab_spectra_base):
    def __init__(self, parent_widget, backend, n_tdcs: int = 40):
        super().__init__(
            parent_widget,
            backend,
            n_tdcs=n_tdcs,
            tab_name="ADC Spectra",
            hist_attr="adc_hist",
            bins_attr="adc_bins",          # optional; inferred if missing (DecodeSnapshot)
            x_label="ADC time",
            x_units="ns",
            x_scale=ADC_LSB_NS,
            title_prefix="ADC",
        )


class tab_tdc_spectra(tab_spectra_base):
    def __init__(self, parent_widget, backend, n_tdcs: int = 40):
        super().__init__(
            parent_widget,
            backend,
            n_tdcs=n_tdcs,
            tab_name="TDC Spectra",
            hist_attr="tdc_hist",
            bins_attr="tdc_bins",          # optional; inferred if missing (DecodeSnapshot)
            x_label="TDC time",
            x_units="ns",
            x_scale=ADC_LSB_NS,
            title_prefix="TDC",
        )


class tab_adc_channels(tab_channel_spectra_base):
    def __init__(self, parent_widget, backend, n_tdcs: int = 40, n_channels: int = 24):
        super().__init__(
            parent_widget,
            backend,
            n_tdcs=n_tdcs,
            n_channels=n_channels,
            tab_name="ADC Channels",
            hist_attr="adc_ch_hist",
            bins_attr="ch_adc_bins",
            x_label="ADC time",
            x_units="ns",
            x_scale=ADC_LSB_NS,
            title_prefix="ADC",
        )


class tab_tdc_channels(tab_channel_spectra_base):
    def __init__(self, parent_widget, backend, n_tdcs: int = 40, n_channels: int = 24):
        super().__init__(
            parent_widget,
            backend,
            n_tdcs=n_tdcs,
            n_channels=n_channels,
            tab_name="TDC Channels",
            hist_attr="tdc_ch_hist",
            bins_attr="ch_tdc_bins",
            x_label="TDC time",
            x_units="ns",
            x_scale=ADC_LSB_NS,
            title_prefix="TDC",
        )


class tab_adc_channel_hits(tab_channel_hits_base):
    def __init__(self, parent_widget, backend, n_tdcs: int = 40):
        super().__init__(
            parent_widget,
            backend,
            n_tdcs=n_tdcs,
            tab_name="ADC CH Hits",
            ch_hist_attr="adc_ch_hist",
            title_prefix="CH hits",
        )
