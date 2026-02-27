# tab_capture.py
from PyQt5 import QtWidgets, QtCore
import os
import pcapy  # pcapy-ng

SETTINGS_FILE = "capture_settings.ini"


class tab_capture(QtCore.QObject):
    def __init__(self, parent_widget, backend=None):
        super().__init__(parent_widget)
        self.parent = parent_widget
        self.backend = backend


        settings_path = os.path.join(os.getcwd(), SETTINGS_FILE)
        self.settings = QtCore.QSettings(settings_path, QtCore.QSettings.IniFormat)

        self._build_ui()
        self._load_settings()
        self.refresh_devices()

        if self.backend is not None:
            self.backend.stats.connect(self.update_stats)
            self.backend.analysis_1hz.connect(self._on_decode_1hz)

        self._last_decode = None  # last DecodeSnapshot

    # ------------------------------------------------------------------

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self.parent)

        # device row
        dev_row = QtWidgets.QHBoxLayout()
        layout.addLayout(dev_row)

        dev_row.addWidget(QtWidgets.QLabel("Device:"))

        self.combo_iface = QtWidgets.QComboBox()
        self.combo_iface.setMinimumWidth(260)
        self.combo_iface.currentIndexChanged.connect(self._on_device_changed)
        dev_row.addWidget(self.combo_iface, 1)

        self.btn_refresh = QtWidgets.QPushButton("Refresh")
        self.btn_refresh.clicked.connect(self.refresh_devices)
        dev_row.addWidget(self.btn_refresh)

        # form
        form = QtWidgets.QFormLayout()
        layout.addLayout(form)

        self.edit_filter = QtWidgets.QLineEdit()
        self.edit_filter.setText("ether src ff:ff:ff:c7:05:01")
        self.edit_filter.editingFinished.connect(self._save_settings)
        form.addRow("BPF filter:", self.edit_filter)

        self.edit_outdir = QtWidgets.QLineEdit()
        self.edit_outdir.setText(os.getcwd())
        self.edit_outdir.editingFinished.connect(self._save_settings)
        form.addRow("Output dir:", self.edit_outdir)

        run_row = QtWidgets.QHBoxLayout()
        self.spin_run = QtWidgets.QSpinBox()
        self.spin_run.setRange(1, 99999999)
        self.spin_run.setValue(1)
        self.spin_run.valueChanged.connect(self._on_run_changed)
        run_row.addWidget(self.spin_run)
        run_row.addStretch(1)
        form.addRow("Next run #:", run_row)

        # buttons
        btns = QtWidgets.QHBoxLayout()
        layout.addLayout(btns)

        self.btn_start = QtWidgets.QPushButton("Start")
        self.btn_start.clicked.connect(self.start)
        btns.addWidget(self.btn_start)

        self.btn_stop = QtWidgets.QPushButton("Stop")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop)
        btns.addWidget(self.btn_stop)
        #replay botton
        self.btn_replay = QtWidgets.QPushButton("Replay .dat...")
        self.btn_replay.clicked.connect(self.replay_dat)
        btns.addWidget(self.btn_replay)
        # capture stats
        stats = QtWidgets.QGridLayout()
        layout.addLayout(stats)

        self.lab_total = QtWidgets.QLabel("0")
        self.lab_lost = QtWidgets.QLabel("0")
        self.lab_bytes = QtWidgets.QLabel("0")
        self.lab_file = QtWidgets.QLabel("-")

        stats.addWidget(QtWidgets.QLabel("Total packets:"), 0, 0)
        stats.addWidget(self.lab_total, 0, 1)
        stats.addWidget(QtWidgets.QLabel("Lost (total):"), 1, 0)
        stats.addWidget(self.lab_lost, 1, 1)
        stats.addWidget(QtWidgets.QLabel("Buffered bytes (total):"), 2, 0)
        stats.addWidget(self.lab_bytes, 2, 1)
        stats.addWidget(QtWidgets.QLabel("Current file:"), 3, 0)
        stats.addWidget(self.lab_file, 3, 1)

        # decode stats
        dec = QtWidgets.QGridLayout()
        layout.addLayout(dec)

        self.lab_hdr = QtWidgets.QLabel("0")
        self.lab_trl = QtWidgets.QLabel("0")
        self.lab_trg = QtWidgets.QLabel("0")
        self.lab_hit = QtWidgets.QLabel("0")
        self.lab_evbuf = QtWidgets.QLabel("0")

        self.lab_err_eid = QtWidgets.QLabel("0")
        self.lab_err_hit = QtWidgets.QLabel("0")
        self.lab_err_mtrl = QtWidgets.QLabel("0")
        self.lab_err_mhdr = QtWidgets.QLabel("0")

        row = 0
        dec.addWidget(QtWidgets.QLabel("Headers:"), row, 0)
        dec.addWidget(self.lab_hdr, row, 1)
        dec.addWidget(QtWidgets.QLabel("Trailers:"), row, 2)
        dec.addWidget(self.lab_trl, row, 3)

        row += 1
        dec.addWidget(QtWidgets.QLabel("Triggers:"), row, 0)
        dec.addWidget(self.lab_trg, row, 1)
        dec.addWidget(QtWidgets.QLabel("Hits (total):"), row, 2)
        dec.addWidget(self.lab_hit, row, 3)

        row += 1
        dec.addWidget(QtWidgets.QLabel("Events buffered:"), row, 0)
        dec.addWidget(self.lab_evbuf, row, 1)

        row += 1
        dec.addWidget(QtWidgets.QLabel("Err: event_id mismatch:"), row, 0)
        dec.addWidget(self.lab_err_eid, row, 1)
        dec.addWidget(QtWidgets.QLabel("Err: hit_count mismatch:"), row, 2)
        dec.addWidget(self.lab_err_hit, row, 3)

        row += 1
        dec.addWidget(QtWidgets.QLabel("Err: missing trailer:"), row, 0)
        dec.addWidget(self.lab_err_mtrl, row, 1)
        dec.addWidget(QtWidgets.QLabel("Err: missing header:"), row, 2)
        dec.addWidget(self.lab_err_mhdr, row, 3)

        layout.addStretch(1)
    
    def replay_dat(self):
        if self.backend is None:
            return

        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self.parent,
            "Select a saved .dat file to replay",
            os.getcwd(),
            "DAT files (*.dat);;All files (*.*)",
        )
        if not path:
            return

        self.backend.start_replay_dat(path, max_events_in_ram=256, max_mb=0)

    # ------------------------------------------------------------------
    # settings

    def _load_settings(self):
        self._last_device = self.settings.value("last/last_device", "", type=str)
        last_filter = self.settings.value("last/last_filter", "", type=str)
        last_outdir = self.settings.value("last/out_dir", "", type=str)

        if last_filter:
            self.edit_filter.setText(last_filter)
        if last_outdir:
            self.edit_outdir.setText(last_outdir)

        next_run = self.settings.value("run/next_run", 1, type=int)
        if next_run < 1:
            next_run = 1
        self.spin_run.setValue(int(next_run))

    def _save_settings(self):
        self.settings.setValue("last/last_device", self.current_device())
        self.settings.setValue("last/last_filter", self.edit_filter.text().strip())
        self.settings.setValue("last/out_dir", self.edit_outdir.text().strip())
        self.settings.setValue("run/next_run", int(self.spin_run.value()))
        self.settings.sync()

    def _on_run_changed(self, _val: int):
        self._save_settings()

    # ------------------------------------------------------------------
    # devices

    def refresh_devices(self):
        self.combo_iface.blockSignals(True)
        self.combo_iface.clear()

        try:
            devs = pcapy.findalldevs()
        except Exception as e:
            self.combo_iface.addItem("ERROR listing devices")
            self.combo_iface.blockSignals(False)
            print(f"[ERROR] pcapy.findalldevs() failed: {repr(e)}")
            return

        for d in devs:
            self.combo_iface.addItem(d)

        self.combo_iface.blockSignals(False)

        if not devs:
            return

        if self._last_device and self._last_device in devs:
            self.combo_iface.setCurrentIndex(devs.index(self._last_device))
        else:
            pick = next((d for d in devs if d != "lo"), devs[0])
            self.combo_iface.setCurrentIndex(devs.index(pick))

    def current_device(self) -> str:
        return self.combo_iface.currentText().strip()

    def _on_device_changed(self, _idx: int):
        dev = self.current_device()
        if dev and "ERROR" not in dev:
            self._save_settings()

    # ------------------------------------------------------------------
    # run control

    def _allocate_run_number(self) -> int:
        run = int(self.spin_run.value())
        self.spin_run.setValue(run + 1)
        return run

    def start(self):
        if self.backend is None:
            print("[ERROR] Backend is not initialized.")
            return

        dev = self.current_device()
        bpf = self.edit_filter.text().strip()
        out_dir = self.edit_outdir.text().strip() or os.getcwd()

        if not dev or "ERROR" in dev:
            print("[ERROR] No valid device selected.")
            return

        self._save_settings()

        run = self._allocate_run_number()
        out_path = self.backend.make_out_path(out_dir, run)

        print(f"Starting capture on {dev}")
        print(f"Filter: {bpf}")
        print(f"Output: {out_path}")

        self.backend.start_capture(dev, bpf, out_path)

        self.lab_file.setText(os.path.basename(out_path))
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)

    def stop(self):
        if self.backend:
            self.backend.stop_capture()

        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)

    # ------------------------------------------------------------------
    # slots

    @QtCore.pyqtSlot(int, int, int, str)
    def update_stats(self, total_packets, lost_total, bytes_total, filename):
        self.lab_total.setText(str(total_packets))
        self.lab_lost.setText(str(lost_total))
        self.lab_bytes.setText(str(bytes_total))
        self.lab_file.setText(filename)

    @QtCore.pyqtSlot(object)
    def _on_decode_1hz(self, snap):
        self._last_decode = snap

        self.lab_hdr.setText(str(int(snap.headers)))
        self.lab_trl.setText(str(int(snap.trailers)))
        self.lab_trg.setText(str(int(snap.triggers)))
        self.lab_hit.setText(str(int(snap.hits_total)))
        self.lab_evbuf.setText(str(int(snap.events_buffered)))

        self.lab_err_eid.setText(str(int(snap.err_event_id)))
        self.lab_err_hit.setText(str(int(snap.err_hit_count)))
        self.lab_err_mtrl.setText(str(int(snap.err_missing_trailer)))
        self.lab_err_mhdr.setText(str(int(snap.err_missing_header)))
