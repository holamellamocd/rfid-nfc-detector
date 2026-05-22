import glob
import logging
import re
import threading

from . import usb_vid
from .base import BaseReader, CardInfo

# USB vendor IDs that belong to Proxmark3 hardware.
_PM3_VIDS: frozenset[int] = frozenset({
    0x9AC4,  # iceman RDV4
    0x2D99,  # older Proxmark3
})

# USB vendor IDs that are common USB-serial bridge chips used in UHF readers
# (FTDI, CH340, CP210x, PL2303).  Ports with these VIDs and no PM3 match are
# almost certainly NOT a Proxmark3 and should be left for the UHF reader.
_UHF_BRIDGE_VIDS: frozenset[int] = frozenset({0x0403, 0x1A86, 0x10C4, 0x067B})

logger = logging.getLogger(__name__)

ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
PM3_PROMPT = re.compile(r"pm3\s*-->")

# Ordered most-specific first so longer matches win.
HF_PROTOCOLS = [
    ("MIFARE Classic 4K", "MIFARE Classic 4K"),
    ("MIFARE Classic 1K", "MIFARE Classic 1K"),
    ("MIFARE Ultralight C", "MIFARE Ultralight C"),
    ("MIFARE Ultralight", "MIFARE Ultralight"),
    ("NTAG 213", "NTAG 213"),
    ("NTAG 215", "NTAG 215"),
    ("NTAG 216", "NTAG 216"),
    ("NTAG", "NTAG (NXP)"),
    ("MIFARE DESFire EV3", "MIFARE DESFire EV3"),
    ("MIFARE DESFire EV2", "MIFARE DESFire EV2"),
    ("MIFARE DESFire EV1", "MIFARE DESFire EV1"),
    ("MIFARE DESFire", "MIFARE DESFire"),
    ("iCLASS", "HID iCLASS"),
    ("FeliCa", "Sony FeliCa"),
    ("ISO 15693", "ISO 15693"),
    ("SRIX", "ST SRIX"),
    ("ISO 14443-B", "ISO 14443-B"),
]

LF_PROTOCOLS = [
    ("EM4x05", "EM4x05 / EM4x69"),
    ("EM410x", "EM4100 / EM4102"),
    ("EM4x", "EM4xxx"),
    ("HID Prox", "HID Prox"),
    ("Indala", "Indala"),
    ("AWID", "AWID"),
    ("Gallagher", "Gallagher"),
    ("Keri", "Keri Systems"),
    ("Viking", "Viking"),
    ("Paradox", "Paradox"),
    ("Pyramid", "Pyramid"),
    ("FDX-B", "FDX-B (ISO 11784)"),
    ("Nedap", "Nedap"),
    ("T55", "T55xx (Programmable)"),
]


def find_proxmark3_ports() -> list[str]:
    ports: list[str] = []
    # ttyACM* devices are always CDC-ACM (modern PM3 RDV4 firmware).
    ports.extend(sorted(glob.glob("/dev/ttyACM*")))
    # ttyUSB* — only include if VID matches known PM3 or VID is unreadable.
    for port in sorted(glob.glob("/dev/ttyUSB*")):
        vid = usb_vid(port)
        if vid is None or vid in _PM3_VIDS:
            ports.append(port)
    return ports


class Proxmark3Reader(BaseReader):
    def __init__(self, port: str):
        self.port = port
        self.id = port
        self.name = f"Proxmark3 ({port})"
        self._child = None
        self._lock = threading.Lock()
        self._connected = False
        self._connect()

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def _connect(self) -> bool:
        try:
            import pexpect

            self._child = pexpect.spawn(
                f"pm3 --port {self.port} --flush",
                encoding="utf-8",
                codec_errors="replace",
                timeout=30,
            )
            idx = self._child.expect([PM3_PROMPT, pexpect.TIMEOUT, pexpect.EOF])
            self._connected = idx == 0
        except Exception as exc:
            logger.warning("Proxmark3 connect failed on %s: %s", self.port, exc)
            self._connected = False
        return self._connected

    def _run(self, cmd: str, timeout: int = 15) -> str:
        if not self._connected or self._child is None:
            return ""
        try:
            import pexpect

            self._child.sendline(cmd)
            idx = self._child.expect([PM3_PROMPT, pexpect.TIMEOUT, pexpect.EOF], timeout=timeout)
            if idx == 0:
                return ANSI_ESCAPE.sub("", self._child.before)
            return ""
        except Exception as exc:
            logger.warning("Proxmark3 command error on %s: %s", self.port, exc)
            self._connected = False
            return ""

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def scan(self) -> CardInfo | None:
        if not self._connected:
            self._connect()
            if not self._connected:
                return None

        with self._lock:
            hf_out = self._run("hf search", timeout=12)
            if hf_out:
                card = self._parse_hf(hf_out)
                if card:
                    return card

            lf_out = self._run("lf search", timeout=12)
            if lf_out:
                return self._parse_lf(lf_out)

        return None

    def close(self):
        if self._child:
            try:
                self._child.sendline("quit")
                self._child.close()
            except Exception:
                pass

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "type": "proxmark3",
            "port": self.port,
            "connected": self._connected,
        }

    # ------------------------------------------------------------------
    # HF parsing
    # ------------------------------------------------------------------

    def _parse_hf(self, output: str) -> CardInfo | None:
        protocol = self._identify_hf(output)
        if not protocol:
            return None

        uid_m = re.search(r"\[.\] {0,2}UID\s*:\s*([\dA-Fa-f ]+)", output)
        uid = uid_m.group(1).strip().replace(" ", ":") if uid_m else None

        atqa_m = re.search(r"ATQA\s*:\s*([\dA-Fa-f ]+)", output)
        sak_m = re.search(r"SAK\s*:\s*([\dA-Fa-f]+)", output)

        return CardInfo(
            frequency="13.56 MHz",
            protocol=protocol,
            uid=uid,
            raw_details={
                "atqa": atqa_m.group(1).strip() if atqa_m else None,
                "sak": sak_m.group(1).strip() if sak_m else None,
            },
        )

    def _identify_hf(self, output: str) -> str | None:
        lo = output.lower()
        for search, label in HF_PROTOCOLS:
            if search.lower() in lo:
                return label
        if "[+]" in output and "uid" in lo:
            return "ISO 14443-A (Unknown)"
        return None

    # ------------------------------------------------------------------
    # LF parsing
    # ------------------------------------------------------------------

    def _parse_lf(self, output: str) -> CardInfo | None:
        protocol = self._identify_lf(output)
        if not protocol:
            return None

        uid = None
        for pattern in (
            r"EM TAG ID\s*:\s*(\S+)",
            r"Card ID\s*:\s*(\S+)",
            r"Raw\s*:\s*(\S+)",
        ):
            m = re.search(pattern, output, re.IGNORECASE)
            if m:
                uid = m.group(1)
                break

        details: dict = {}
        fc_cn = re.search(r"FC\s*:\s*(\d+)\s+CN\s*:\s*(\d+)", output, re.IGNORECASE)
        if fc_cn:
            details["facility_code"] = fc_cn.group(1)
            details["card_number"] = fc_cn.group(2)

        return CardInfo(frequency="125 kHz", protocol=protocol, uid=uid, raw_details=details)

    def _identify_lf(self, output: str) -> str | None:
        lo = output.lower()
        for search, label in LF_PROTOCOLS:
            if search.lower() in lo:
                return label
        if "[+]" in output and ("valid" in lo or "found" in lo):
            return "LF Tag (Unknown)"
        return None
