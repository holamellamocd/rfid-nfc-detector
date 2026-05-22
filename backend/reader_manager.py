import asyncio
import logging
import threading
import time

from readers.pcsc import PCSCReader, list_pcsc_readers
from readers.proxmark3 import Proxmark3Reader, find_proxmark3_ports
from readers.uhf import UHFReader, find_uhf_candidate_ports

logger = logging.getLogger(__name__)

# Seconds to wait before retrying a failed reader
_READER_ERROR_BACKOFF = 3
# Seconds between re-discovery sweeps
_DISCOVERY_INTERVAL = 5
# Seconds to debounce repeated detections of the same card
_DEBOUNCE = 2


class ReaderManager:
    def __init__(self, event_queue: asyncio.Queue, loop: asyncio.AbstractEventLoop):
        self._queue = event_queue
        self._loop = loop
        self._readers: dict[str, object] = {}
        self._lock = threading.Lock()
        self._running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self):
        self._running = True
        self._discover()
        threading.Thread(target=self._discovery_loop, daemon=True).start()

    def stop(self):
        self._running = False
        with self._lock:
            for reader in self._readers.values():
                reader.close()

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def _discovery_loop(self):
        while self._running:
            time.sleep(_DISCOVERY_INTERVAL)
            self._discover()

    def _discover(self):
        candidates: list[tuple[str, object]] = []

        for port in find_proxmark3_ports():
            candidates.append((port, lambda p=port: Proxmark3Reader(p)))

        for name in list_pcsc_readers():
            candidates.append((name, lambda n=name: PCSCReader(n)))

        for port in find_uhf_candidate_ports():
            candidates.append((port, lambda p=port: UHFReader(p)))

        for reader_id, factory in candidates:
            with self._lock:
                if reader_id in self._readers:
                    continue
                try:
                    reader = factory()
                    self._readers[reader_id] = reader
                    self._emit({"type": "reader_added", "reader": reader.to_dict()})
                    threading.Thread(
                        target=self._reader_loop,
                        args=(reader,),
                        daemon=True,
                    ).start()
                except Exception as exc:
                    logger.warning("Failed to initialise reader %s: %s", reader_id, exc)

    # ------------------------------------------------------------------
    # Per-reader scan loop
    # ------------------------------------------------------------------

    def _reader_loop(self, reader):
        while self._running:
            try:
                card = reader.scan()
                if card:
                    self._emit(
                        {
                            "type": "card_detected",
                            "reader_id": reader.id,
                            "card": card.to_dict(),
                        }
                    )
                    # Debounce: don't spam the same card over and over
                    time.sleep(_DEBOUNCE)
                else:
                    time.sleep(0.3)
            except Exception as exc:
                logger.error("Reader %s error: %s", reader.id, exc)
                self._emit(
                    {"type": "reader_error", "reader_id": reader.id, "error": str(exc)}
                )
                time.sleep(_READER_ERROR_BACKOFF)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _emit(self, event: dict):
        asyncio.run_coroutine_threadsafe(self._queue.put(event), self._loop)

    def get_state(self) -> list[dict]:
        with self._lock:
            return [r.to_dict() for r in self._readers.values()]
