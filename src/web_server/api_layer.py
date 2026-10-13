"""The FastAPI layer: routes, the WebSocket endpoint, the uvicorn thread."""
import asyncio
import logging
import os
import threading
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

import src.web_server.data_layer as data_layer
from src.times import epoch_seconds, hhmmss, now_utc
from src.web_server.data_layer import (
    INDEX_CANDIDATE_SYMBOLS,
    INDEX_DISPLAY_NAMES,
    INDEX_TICKERS,
    PROJECT_ROOT,
    TRACKED_INDEXES,
    _query_historical_ticks_safe,
    compute_recommendation,
    decision_buffer,
    index_store,
    market_buffer,
)

logger = logging.getLogger(__name__)


# ============================================================================
# WebSocket Connection Manager
# ============================================================================

class ConnectionManager:
    """Manages active WebSocket connections."""

    def __init__(self):
        self.active_connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, register: bool = True):
        await websocket.accept()
        if register:
            async with self._lock:
                self.active_connections.add(websocket)
            logger.info("WebSocket client connected. Active: %d", len(self.active_connections))

    async def register(self, websocket: WebSocket):
        async with self._lock:
            self.active_connections.add(websocket)
        logger.info("WebSocket client registered. Active: %d", len(self.active_connections))

    async def disconnect(self, websocket: WebSocket):
        async with self._lock:
            self.active_connections.discard(websocket)
        logger.info("WebSocket client disconnected. Active: %d", len(self.active_connections))

    async def broadcast_json(self, message: dict[str, Any]):
        async with self._lock:
            clients = list(self.active_connections)

        if not clients:
            return

        dead_clients = []
        for ws in clients:
            try:
                await ws.send_json(message)
            except (WebSocketDisconnect, ConnectionError, OSError):
                dead_clients.append(ws)

        if dead_clients:
            async with self._lock:
                for ws in dead_clients:
                    self.active_connections.discard(ws)


manager = ConnectionManager()


async def broadcast_loop(interval: float = 1.0):
    """Broadcasts market state and decisions every 1 second."""
    logger.info("WebSocket broadcast loop started (%ss interval)", interval)
    while True:
        try:
            symbols = market_buffer.get_symbols()
            symbol_summaries = []
            decisions = []

            for sym in symbols:
                summary = market_buffer.get_summary(sym)
                rec = compute_recommendation(sym)
                summary["recommendation"] = rec["action"]
                summary["confidence"] = rec["confidence"]
                symbol_summaries.append(summary)
                decisions.append(rec)

            # Get current market indexes
            market_indexes = await fetch_tracked_indexes()

            payload = {
                "type": "update",
                "timestamp": epoch_seconds(now_utc()),
                "time_str": hhmmss(now_utc()),
                "symbols": symbol_summaries,
                "decisions": decisions,
                "market_indexes": market_indexes,
                "marketIndexes": market_indexes
            }

            await manager.broadcast_json(payload)
        except asyncio.CancelledError:
            break
        except Exception as e:
            # Loop boundary: one tick that fails must not end the broadcast loop,
            # and the tick can fail from any of the buffer, DB and websocket paths
            # above, so this is the one place a broad catch is deliberate.
            logger.debug("Broadcast loop tick error: %s", e)

        await asyncio.sleep(interval)


# ============================================================================
# FastAPI Application & Lifespan
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: ensure index store is loaded
    index_store.load()
    task = asyncio.create_task(broadcast_loop(interval=1.0))
    yield
    # Shutdown
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="KDR Real-Time Trading Dashboard", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_HTML_PATH = os.path.join(PROJECT_ROOT, "static", "dashboard.html")
TEST_WS_HTML_PATH = os.path.join(PROJECT_ROOT, "static", "test-websocket.html")


@app.get("/", response_class=HTMLResponse)
@app.get("/symbol/{symbol}", response_class=HTMLResponse)
async def serve_dashboard(symbol: str | None = None):
    if os.path.exists(STATIC_HTML_PATH):
        with open(STATIC_HTML_PATH, encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Dashboard HTML template not found.</h1>", status_code=404)


@app.get("/test-websocket", response_class=HTMLResponse)
@app.get("/test-websocket.html", response_class=HTMLResponse)
async def serve_test_websocket():
    if os.path.exists(TEST_WS_HTML_PATH):
        with open(TEST_WS_HTML_PATH, encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Test WebSocket HTML template not found.</h1>", status_code=404)


@app.get("/api/status")
async def get_status():
    symbols = market_buffer.get_symbols()
    return {
        "status": "online",
        "symbols_tracked": len(symbols),
        "symbols": symbols,
        "timestamp": epoch_seconds(now_utc())
    }


@app.get("/api/symbols")
async def get_symbols():
    symbols = market_buffer.get_symbols()
    results = []
    for sym in symbols:
        summary = market_buffer.get_summary(sym)
        rec = compute_recommendation(sym)
        summary["recommendation"] = rec["action"]
        summary["confidence"] = rec["confidence"]
        results.append(summary)
    return JSONResponse(results)


@app.get("/api/market-index/{index}")
async def get_market_index(index: str) -> dict[str, Any]:
    """
    Returns market index data for specific indices.
    Data is persisted so valuations are never $0.00 when markets are closed.
    Fast retrieval without historical database lookups.
    """
    index = index.upper()
    display_name = INDEX_DISPLAY_NAMES.get(index, index)
    ticker = INDEX_TICKERS.get(index, index)
    clean_ticker = ticker[1:] if ticker.startswith("^") else ticker
    candidates = INDEX_CANDIDATE_SYMBOLS.get(index, [index, clean_ticker])

    # Check live in-memory buffer ONLY (fast, non-blocking, no historical DB query)
    for sym in candidates:
        tick = market_buffer.get_latest_tick(sym)
        if tick and tick.price > 0:
            open_p = market_buffer._open_prices.get(sym.upper(), tick.price)
            change = tick.price - open_p if open_p > 0 else 0.0
            change_pct = (change / open_p * 100.0) if open_p > 0 else 0.0
            time_str = hhmmss(tick.timestamp)
            index_store.update_index(
                index=index,
                current_price=tick.price,
                change=change,
                change_percent=change_pct,
                last_update=time_str,
                price_type="live"
            )
            break

    # Return persisted latest value - fast, non-blocking, never 0.00
    res = index_store.get_index(index)
    res["displayName"] = display_name
    res["ticker"] = clean_ticker
    return res


async def fetch_tracked_indexes() -> list[dict[str, Any]]:
    """Fetch data for all tracked market indices from persistent store (never $0.00).
    Uses fast in-memory checks without historical database lookups.
    """
    for idx in TRACKED_INDEXES:
        candidates = INDEX_CANDIDATE_SYMBOLS.get(idx, [idx])
        for sym in candidates:
            # Check live in-memory buffer ONLY (no historical DB lookup)
            tick = market_buffer.get_latest_tick(sym)
            if tick and tick.price > 0:
                open_p = market_buffer._open_prices.get(sym.upper(), tick.price)
                change = tick.price - open_p if open_p > 0 else 0.0
                change_pct = (change / open_p * 100.0) if open_p > 0 else 0.0
                time_str = hhmmss(tick.timestamp)
                index_store.update_index(
                    index=idx,
                    current_price=tick.price,
                    change=change,
                    change_percent=change_pct,
                    last_update=time_str,
                    price_type="live"
                )
                break
    return index_store.get_all(TRACKED_INDEXES)


@app.get("/api/market-indexes")
async def get_all_market_indexes() -> list[dict[str, Any]]:
    """
    Returns data for all market indexes (VIX, DJIA, S&P500, NASDAQ, Russell 2000)
    in a single request for efficiency.
    """
    return await fetch_tracked_indexes()


def _window_start(duration: str | None, now: datetime) -> datetime | None:
    """The earliest timestamp the requested duration window covers, or None for no window."""
    if duration == "1h":
        return now - timedelta(hours=1)
    if duration == "24h":
        return now - timedelta(hours=24)
    if duration == "5d":
        return now - timedelta(days=5)
    if duration == "30d":
        return now - timedelta(days=30)
    if duration == "1y":
        return now - timedelta(days=365)
    if duration == "ytd":
        return datetime(now.year, 1, 1, tzinfo=UTC)
    return None


def _merge_ticks(
    historical: list[dict[str, Any]], buffered: list[dict[str, Any]], limit: int | None
) -> list[dict[str, Any]]:
    """DB ticks plus buffer ticks, deduplicated by (timestamp, price), oldest first."""
    seen_db_keys = set()
    all_ticks = []
    for t in historical:
        ts = float(t.get("timestamp", 0))
        seen_db_keys.add((round(ts, 2), t.get("price")))
        all_ticks.append(t)

    for t in buffered:
        ts = float(t.get("timestamp", 0))
        if (round(ts, 2), t.get("price")) not in seen_db_keys:
            all_ticks.append(t)

    all_ticks.sort(key=lambda x: x.get("timestamp", 0))
    if limit and len(all_ticks) > limit:
        all_ticks = all_ticks[-limit:]
    return all_ticks


def _moving_average(prices: list[float], window: int) -> float:
    """Mean of the last ``window`` prices, or the last price when there are fewer."""
    if len(prices) >= window:
        return round(sum(prices[-window:]) / float(window), 2)
    return prices[-1] if prices else 0.0


@app.get("/api/symbols/{symbol}")
async def get_symbol_detail(
    symbol: str,
    duration: str | None = "24h",
    limit: int | None = 1000
):
    symbol = symbol.upper()
    summary = market_buffer.get_summary(symbol, duration=duration or "24h")

    # Determine start_time based on requested duration window
    now = now_utc()
    start_time = _window_start(duration, now)

    historical_ticks = []
    db = data_layer.get_db_manager()
    if db and hasattr(db, "get_historical_market_data"):
        historical_ticks = await _query_historical_ticks_safe(
            db=db,
            symbol=symbol,
            start_time=start_time,
            limit=limit or 1000
        )

    # In-memory buffer ticks
    buffer_ticks = market_buffer.get_ticks(symbol, limit=limit or 500)
    if start_time is not None:
        start_ts = start_time.timestamp()
        buffer_ticks = [t for t in buffer_ticks if t.get("timestamp", 0) >= start_ts]

    # Combine historical db data and in-memory buffer ticks without duplicates (O(N+M))
    all_ticks = _merge_ticks(historical_ticks, buffer_ticks, limit)

    # Fallback seed tick if no ticks are available yet but summary has a live price
    if not all_ticks and summary.get("currentPrice", 0.0) > 0:
        all_ticks.append({
            "symbol": symbol,
            "price": summary["currentPrice"],
            "size": summary.get("volume", 0.0),
            "timestamp": epoch_seconds(now_utc()),
            "time_str": hhmmss(now_utc())
        })

    decision = compute_recommendation(symbol)
    decisions_history = decision_buffer.get_history(symbol, limit=20)

    # Compute moving averages (50-day and 200-day) if enough data
    prices = [t.get("price", 0.0) for t in all_ticks]
    return JSONResponse({
        "summary": summary,
        "recommendation": decision,
        "ticks": all_ticks,
        "decisions": decisions_history,
        "moving_averages": {
            "200_day": _moving_average(prices, 200),
            "50_day": _moving_average(prices, 50)
        }
    })


@app.websocket("/ws")
@app.websocket("/ws/updates")
async def websocket_endpoint(websocket: WebSocket):
    logger.info("WebSocket request received")
    await manager.connect(websocket, register=False)
    try:
        logger.info("WebSocket connection accepted, preparing initial state")
        # Immediately send current state upon connection
        logger.info("Fetching symbols from market buffer")
        symbols = market_buffer.get_symbols()
        logger.info(f"Found {len(symbols)} symbols to report")

        summaries = []
        decisions = []
        for sym in symbols:
            s = market_buffer.get_summary(sym)
            r = compute_recommendation(sym)
            s["recommendation"] = r["action"]
            s["confidence"] = r["confidence"]
            summaries.append(s)
            decisions.append(r)

        # Get initial market indexes
        market_indexes = await fetch_tracked_indexes()

        logger.info(f"Sending init message with {len(summaries)} symbols and {len(market_indexes)} market indexes")
        await websocket.send_json({
            "type": "init",
            "timestamp": epoch_seconds(now_utc()),
            "time_str": hhmmss(now_utc()),
            "symbols": summaries,
            "decisions": decisions,
            "market_indexes": market_indexes,
            "marketIndexes": market_indexes
        })
        await manager.register(websocket)
        logger.info("Init message sent successfully, entering receive loop")

        while True:
            # Keep socket open and accept incoming ping/client messages
            msg = await websocket.receive_text()
            logger.debug(f"Received message from client: {msg[:50]}")
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected normally")
        await manager.disconnect(websocket)
    except Exception as e:
        # Connection boundary: whatever the handlers above raised, the socket has
        # to close cleanly rather than take the server down with it.
        logger.error(f"WebSocket error: {type(e).__name__}: {e}", exc_info=True)
        await manager.disconnect(websocket)


# ============================================================================
# Server Thread Runner
# ============================================================================

_server_instance: uvicorn.Server | None = None
_server_thread: threading.Thread | None = None


def is_port_available(port: int, host: str = "0.0.0.0") -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def start_web_server_thread(host: str = "0.0.0.0", port: int = 8001) -> uvicorn.Server | None:
    """
    Starts the FastAPI web server in a background daemon thread.
    Returns the running uvicorn.Server instance.
    """
    global _server_instance, _server_thread

    if _server_thread is not None and _server_thread.is_alive():
        logger.info("Web server thread already running.")
        return _server_instance

    if not is_port_available(port, host):
        logger.info("Port %d is already in use. Web server thread start skipped.", port)
        return _server_instance

    config = uvicorn.Config(
        app=app,
        host=host,
        port=port,
        log_level="warning",
        access_log=False
    )
    _server_instance = uvicorn.Server(config)

    _server_thread = threading.Thread(
        target=_server_instance.run,
        daemon=True,
        name="DashboardWebServerThread"
    )
    _server_thread.start()

    logger.info("Real-time Trading Dashboard active at http://localhost:%d", port)
    return _server_instance


def stop_web_server():
    """Stops the running web server thread."""
    global _server_instance, _server_thread
    if _server_instance is not None:
        _server_instance.should_exit = True
        logger.info("Web server shutdown requested.")
    if _server_thread is not None and _server_thread.is_alive():
        try:
            _server_thread.join(timeout=2.0)
        except RuntimeError:
            # Joining a thread that is not joinable (already finished elsewhere,
            # or this thread) is not a shutdown failure.
            pass
    _server_instance = None
    _server_thread = None


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
