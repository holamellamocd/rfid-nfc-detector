import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from reader_manager import ReaderManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)

_event_queue: asyncio.Queue
_clients: set[WebSocket] = set()
_manager: ReaderManager


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _event_queue, _manager
    _event_queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    _manager = ReaderManager(_event_queue, loop)
    _manager.start()
    broadcaster = asyncio.create_task(_broadcast_loop())
    yield
    broadcaster.cancel()
    _manager.stop()


app = FastAPI(lifespan=lifespan)


logger = logging.getLogger(__name__)


async def _broadcast_loop():
    while True:
        event = await _event_queue.get()
        dead: set[WebSocket] = set()
        for client in list(_clients):
            try:
                await client.send_json(event)
            except Exception as exc:
                logger.warning("send_json failed: %s", exc)
                dead.add(client)
        _clients.difference_update(dead)


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    _clients.add(ws)
    # Send snapshot of known readers immediately on connect
    await ws.send_json({"type": "state", "readers": _manager.get_state()})
    try:
        while True:
            await ws.receive_text()  # keep-alive; client may send pings
    except WebSocketDisconnect:
        _clients.discard(ws)


_frontend = Path(__file__).parent.parent / "frontend"
app.mount("/", StaticFiles(directory=str(_frontend), html=True), name="static")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8765, reload=False)
