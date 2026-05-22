import logging

from .base import BaseReader, CardInfo

logger = logging.getLogger(__name__)

# ATR sub-strings that identify card types routed through ACR122U / pcscd.
# The NXP RID is A0 00 00 03 06; the following byte encodes the card family.
_ATR_MAP = [
    ("A0000003060300020000", "MIFARE Classic 4K"),
    ("A0000003060300010000", "MIFARE Classic 1K"),
    ("A0000003060300030000", "MIFARE Ultralight / NTAG"),
    ("A000000306030003", "MIFARE Ultralight / NTAG"),
    ("A0000003060B", "Sony FeliCa"),
    ("A000000306", "NXP NFC Card"),
]


def list_pcsc_readers() -> list[str]:
    try:
        from smartcard.System import readers as _readers

        return [str(r) for r in _readers()]
    except Exception:
        return []


class PCSCReader(BaseReader):
    def __init__(self, reader_name: str):
        self.id = reader_name
        self.name = reader_name
        self._reader_name = reader_name
        self._reader = None
        self._resolve_reader()

    def _resolve_reader(self):
        try:
            from smartcard.System import readers as _readers

            for r in _readers():
                if str(r) == self._reader_name:
                    self._reader = r
                    return
        except Exception:
            pass

    def scan(self) -> CardInfo | None:
        if not self._reader:
            self._resolve_reader()
            if not self._reader:
                logger.debug("No reader resolved for %s", self._reader_name)
                return None

        try:
            conn = self._reader.createConnection()
            conn.connect()
            atr = conn.getATR()
            uid = self._read_uid(conn)
            protocol = self._identify_atr(atr)
            conn.disconnect()

            return CardInfo(
                frequency="13.56 MHz",
                protocol=protocol or "Smart Card (Unknown)",
                uid=uid,
                raw_details={"atr": " ".join(f"{b:02X}" for b in atr)},
            )
        except Exception as exc:
            if "No smart card" not in str(exc) and "card not present" not in str(exc).lower():
                logger.warning("PCSC scan error on %s: %s", self._reader_name, exc)
            return None

    def _read_uid(self, conn) -> str | None:
        try:
            data, sw1, _ = conn.transmit([0xFF, 0xCA, 0x00, 0x00, 0x00])
            if sw1 == 0x90:
                return ":".join(f"{b:02X}" for b in data)
        except Exception:
            pass
        return None

    def _identify_atr(self, atr: list[int]) -> str | None:
        atr_hex = "".join(f"{b:02X}" for b in atr)
        for substring, label in _ATR_MAP:
            if substring in atr_hex:
                return label
        if atr_hex.startswith("3B"):
            return "ISO 14443-B Smart Card"
        return None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "type": "pcsc",
            "port": self.id,
            "connected": self._reader is not None,
        }
