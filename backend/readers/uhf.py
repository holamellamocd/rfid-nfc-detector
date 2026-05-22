"""
UHF Gen2 / ISO 18000-6C reader via the ThingMagic Mercury API.

Tested hardware: SparkFun M6e Nano, ThingMagic M6e Micro, and compatible
modules (any reader whose firmware speaks the Mercury Serial protocol).

Install the Python binding:
    pip install mercuryapi

Set the UHF_REGION environment variable to match your country before
starting the backend:
    UHF_REGION=NA   python main.py    # North America  (902–928 MHz)
    UHF_REGION=EU3  python main.py    # Europe         (865–868 MHz)
    UHF_REGION=AU   python main.py    # Australia      (920–926 MHz)
    UHF_REGION=IN   python main.py    # India          (865–867 MHz)
    UHF_REGION=JP   python main.py    # Japan          (916–921 MHz)
    UHF_REGION=CN   python main.py    # China          (920–925 MHz)
"""

import glob
import logging
import os
import threading

from . import usb_vid
from .base import BaseReader, CardInfo

logger = logging.getLogger(__name__)

# GS1 EPC header byte → human-readable encoding type.
_EPC_TYPES: dict[int, str] = {
    0x30: "SGTIN-96",      # Serialised Global Trade Item Number (retail)
    0x31: "SSCC-96",       # Serial Shipping Container Code
    0x32: "SGLN-96",       # Serialised Global Location Number
    0x33: "GRAI-96",       # Global Returnable Asset Identifier
    0x34: "GIAI-96",       # Global Individual Asset Identifier
    0x35: "GS1-96",
    0x36: "GDTI-96",       # Global Document Type Identifier
    0x38: "GSRN-96",       # Global Service Relation Number
    0x3C: "CPI-96",        # Component / Part Identifier
}

# USB-serial bridge VIDs common in UHF readers (FTDI, CH340, CP210x, PL2303).
_UHF_BRIDGE_VIDS: frozenset[int] = frozenset({0x0403, 0x1A86, 0x10C4, 0x067B})

# Region from environment; default to EU3 (safe worldwide for evaluation).
UHF_REGION: str = os.environ.get("UHF_REGION", "EU3")

# Single-inventory read window in milliseconds.
_READ_MS = 500


def find_uhf_candidate_ports() -> list[str]:
    """
    Return /dev/ttyUSB* ports whose USB VID indicates a serial bridge chip
    (FTDI, CH340, CP210x, PL2303) — the chips found in virtually all
    low-cost UHF reader modules.  Ports with an unknown VID are included too
    so devices on systems without sysfs access are still discovered.
    """
    ports: list[str] = []
    for port in sorted(glob.glob("/dev/ttyUSB*")):
        vid = usb_vid(port)
        if vid is None or vid in _UHF_BRIDGE_VIDS:
            ports.append(port)
    return ports


class UHFReader(BaseReader):
    def __init__(self, port: str):
        self.port = port
        self.id = port
        self.name = f"UHF Gen2 ({port})"
        self._reader = None
        self._lock = threading.Lock()
        self._connected = False
        # Buffer lets a single multi-tag read produce multiple scan() results.
        self._buffer: list[CardInfo] = []
        self._connect()

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def _connect(self) -> bool:
        try:
            import mercury  # type: ignore[import-untyped]

            r = mercury.Reader(f"tmr://{self.port}", baudrate=115200)
            r.set_region(UHF_REGION)
            self._reader = r
            self._connected = True
            logger.info("UHF reader online: %s  region=%s", self.port, UHF_REGION)
        except Exception as exc:
            # Expected for non-Mercury serial devices — log at DEBUG only.
            logger.debug("Not a Mercury UHF reader on %s: %s", self.port, exc)
            self._reader = None
            self._connected = False
        return self._connected

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def scan(self) -> CardInfo | None:
        # Drain any tags buffered from the previous multi-tag inventory.
        if self._buffer:
            return self._buffer.pop(0)

        if not self._connected:
            self._connect()
            if not self._connected:
                return None

        with self._lock:
            tags = self._inventory()

        if not tags:
            return None

        cards = [self._tag_to_card(t) for t in tags]
        self._buffer = cards[1:]
        return cards[0]

    def close(self):
        if self._reader:
            try:
                self._reader.stop_reading()
            except Exception:
                pass

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "type": "uhf",
            "port": self.port,
            "connected": self._connected,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _inventory(self) -> list:
        try:
            return self._reader.read(timeout=_READ_MS) or []
        except Exception as exc:
            logger.warning("UHF inventory error on %s: %s", self.port, exc)
            self._connected = False
            return []

    def _tag_to_card(self, tag) -> CardInfo:
        epc_bytes = bytes(tag.epc)
        epc_hex = epc_bytes.hex().upper()
        uid = ":".join(epc_hex[i : i + 2] for i in range(0, len(epc_hex), 2))

        header = epc_bytes[0] if epc_bytes else 0xFF
        epc_type = _EPC_TYPES.get(header)
        protocol = f"EPC Gen2 — {epc_type}" if epc_type else "EPC Gen2 / ISO 18000-6C"

        details: dict = {"rssi_dbm": getattr(tag, "rssi", None)}
        if getattr(tag, "antenna", None) is not None:
            details["antenna"] = tag.antenna
        if getattr(tag, "read_count", None) is not None:
            details["reads"] = tag.read_count
        if epc_type:
            details["epc_type"] = epc_type

        return CardInfo(
            frequency="860–960 MHz",
            protocol=protocol,
            uid=uid,
            raw_details={k: v for k, v in details.items() if v is not None},
        )
