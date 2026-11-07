"""BrainFlow connection to a Muse S (or the synthetic board for dry runs)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from brainflow.board_shim import BoardIds, BoardShim, BrainFlowInputParams, BrainFlowPresets

AUX = BrainFlowPresets.AUXILIARY_PRESET
EEG = BrainFlowPresets.DEFAULT_PRESET

BOARDS = {
    "muse-s": BoardIds.MUSE_S_BOARD,            # native Bluetooth (built-in BLE)
    "muse-s-bled": BoardIds.MUSE_S_BLED_BOARD,  # BLED112 USB dongle (needs --serial-port)
    "muse-s-athena": BoardIds.MUSE_S_ATHENA_BOARD,
    "muse-2": BoardIds.MUSE_2_BOARD,
    "synthetic": BoardIds.SYNTHETIC_BOARD,
}


@dataclass
class ImuLayout:
    preset: int
    rate_hz: float
    accel: list[int]
    gyro: list[int]
    timestamp: int
    package: int
    marker: int


@dataclass
class EegLayout:
    preset: int
    rate_hz: float
    channels: list[int]
    names: list[str]
    timestamp: int


class MuseDevice:
    def __init__(self, board: str = "muse-s", serial_number: str = "", serial_port: str = "",
                 mac_address: str = "", timeout_s: int = 15):
        self.board_name = board
        self.board_id = int(BOARDS[board])
        params = BrainFlowInputParams()
        params.serial_number = serial_number  # e.g. "MuseS-1A2B"; empty = first Muse found
        params.serial_port = serial_port      # BLED112 only, e.g. "COM3"
        params.mac_address = mac_address
        params.timeout = timeout_s
        self.shim = BoardShim(self.board_id, params)

        self.imu = self._imu_layout()
        self.eeg = self._eeg_layout()

    # -- layouts ---------------------------------------------------------------
    def _imu_layout(self) -> ImuLayout:
        d = BoardShim.get_board_descr(self.board_id, AUX)
        return ImuLayout(AUX, float(d["sampling_rate"]), d["accel_channels"], d["gyro_channels"],
                         d["timestamp_channel"], d["package_num_channel"], d["marker_channel"])

    def _eeg_layout(self) -> EegLayout:
        d = BoardShim.get_board_descr(self.board_id, EEG)
        return EegLayout(EEG, float(d["sampling_rate"]), d["eeg_channels"],
                         d.get("eeg_names", "").split(","), d["timestamp_channel"])

    # -- lifecycle -------------------------------------------------------------
    def start(self, buffer_size: int = 450_000) -> None:
        self.shim.prepare_session()
        self.shim.start_stream(buffer_size)

    def stop(self) -> None:
        try:
            if self.shim.is_prepared():
                try:
                    self.shim.stop_stream()
                finally:
                    self.shim.release_session()
        except Exception:
            pass

    # -- data ------------------------------------------------------------------
    def pull_imu(self) -> np.ndarray:
        """All IMU rows received since the last call, shape (rows, n)."""
        return self.shim.get_board_data(preset=self.imu.preset)

    def pull_eeg(self) -> np.ndarray:
        return self.shim.get_board_data(preset=self.eeg.preset)

    def insert_marker(self, value: float) -> None:
        """Write a marker into both streams so EEG and IMU can be aligned offline."""
        for preset in {self.imu.preset, self.eeg.preset}:
            try:
                self.shim.insert_marker(value, preset)
            except Exception:
                pass
