# DatReplayThread.py
from __future__ import annotations

import os
import time
import queue
from typing import Optional

from PyQt5 import QtCore


class DatReplayThread(QtCore.QThread):
    """
    Replay a saved .dat file produced by this DAQ:
      - file content is raw 5-byte word stream (idle removed)
      - push bytes into analysis queue (like CaptureThread does)
    """

    message = QtCore.pyqtSignal(str)
    stats = QtCore.pyqtSignal(
        int,  # total_packets (not applicable -> use 0)
        int,  # lost_packets (not applicable -> use 0)
        int,  # total_bytes_replayed
        str,  # filename
    )

    def __init__(
        self,
        dat_path: str,
        analysis_q: "queue.Queue[bytes]",
        chunk_size: int = 4 * 1024 * 1024,
        max_mb: int = 0,          # 0 means no limit
        realtime: bool = False,   # True: throttle replay speed
        parent: Optional[QtCore.QObject] = None,
    ):
        super().__init__(parent)
        self.dat_path = str(dat_path)
        self.analysis_q = analysis_q
        self.chunk_size = int(chunk_size)
        self.max_mb = int(max_mb)
        self.realtime = bool(realtime)
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        if not os.path.exists(self.dat_path):
            self.message.emit(f"[ERROR] .dat not found: {self.dat_path}\n")
            return

        fname = os.path.basename(self.dat_path)
        self.message.emit(f"[INFO] Replaying .dat: {self.dat_path}\n")

        max_bytes = (self.max_mb * 1024 * 1024) if self.max_mb > 0 else 0
        read_bytes = 0
        last_stat_emit = time.time()

        t0 = time.time()
        with open(self.dat_path, "rb") as f:
            while not self._stop:
                chunk = f.read(self.chunk_size)
                if not chunk:
                    break

                read_bytes += len(chunk)
                if max_bytes and read_bytes > max_bytes:
                    self.message.emit(f"[WARN] Reached max_mb={self.max_mb}MB, stop early.\n")
                    break

                # push into analysis queue (non-blocking like CaptureThread)
                try:
                    self.analysis_q.put_nowait(bytes(chunk))
                except queue.Full:
                    # if decode is slower, drop analysis data (like capture thread does)
                    pass

                # optional throttle to approximate real-time playback (rough)
                if self.realtime:
                    # assume file was produced in ~real time; simple throttle by bytes/sec is hard
                    # this just prevents GUI from being flooded too fast:
                    time.sleep(0.001)

                now = time.time()
                if now - last_stat_emit >= 0.2:
                    self.stats.emit(
                        0, 0, read_bytes, fname
                    )
                    last_stat_emit = now

        dt = time.time() - t0
        self.stats.emit(0, 0, read_bytes, fname)
        self.message.emit(f"[INFO] Replay finished. bytes={read_bytes}, seconds={dt:.2f}\n")