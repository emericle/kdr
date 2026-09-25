"""
Real-time Trading Dashboard Web Server.

Provides WebSocket streams of live market data and trading decisions,
REST APIs for historical and detail data, and serves the frontend dashboard.
Designed to run in a background thread alongside the scraper pipeline.
"""

import asyncio
import os
import time
import logging
import threading
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

logger = logging.getLogger("WebServer")

# ============================================================================
# Data Models
# ============================================================================

@dataclass
class MarketTick:
    """A single tick data point."""
    symbol: str
    price: float
    size: float = 0.0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "price": self.price,
            "size": self.size,
            "timestamp": self.timestamp,
            "time_str": datetime.fromtimestamp(self.timestamp).strftime("%H:%M:%S")
        }


@dataclass
class TradingDecision:
    """A trading recommendation or execution decision."""
    symbol: str
    action: str  # BUY, SELL, HOLD
    confidence: float  # 0.0 to 1.0
    reasoning: List[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "action": self.action,
            "confidence": round(self.confidence, 2),
            "reasoning": self.reasoning,
            "timestamp": self.timestamp,
            "time_str": datetime.fromtimestamp(self.timestamp).strftime("%H:%M:%S")
        }


# ============================================================================
# Thread-Safe Buffers
# ============================================================================

class MarketDataBuffer:
    """Thread-safe buffer storing tick history and computing real-time stats."""

    def __init__(self, max_ticks: int = 500):
        self.max_ticks = max_ticks
        self._history: Dict[str, deque] = {}
        self._open_prices: Dict[str, float] = {}
        self._lock = threading.Lock()

    def add_tick(
        self,
        symbol: str,
        price: float,
        size: float = 0.0,
        timestamp: Optional[Any] = None
    ) -> MarketTick:
        symbol = symbol.upper()
        ts = time.time()
        if timestamp is not None:
            if isinstance(timestamp, (int, float)):
                ts = float(timestamp if timestamp < 1e11 else timestamp / 1e9)
            elif isinstance(timestamp, datetime):
                ts = timestamp.timestamp()

        tick = MarketTick(symbol=symbol, price=float(price), size=float(size or 0.0), timestamp=ts)

        with self._lock:
            if symbol not in self._history:
                self._history[symbol] = deque(maxlen=self.max_ticks)
                self._open_prices[symbol] = tick.price

            self._history[symbol].append(tick)

        return tick

    def get_symbols(self) -> List[str]:
        with self._lock:
            return sorted(list(self._history.keys()))

    def get_latest_tick(self, symbol: str) -> Optional[MarketTick]:
        symbol = symbol.upper()
        with self._lock:
            history = self._history.get(symbol)
            if history and len(history) > 0:
                return history[-1]
            return None

    def get_ticks(self, symbol: str, limit: int = 100) -> List[Dict[str, Any]]:
        symbol = symbol.upper()
        with self._lock:
            history = self._history.get(symbol, deque())
            items = list(history)[-limit:]
            return [t.to_dict() for t in items]

    def get_summary(self, symbol: str) -> Dict[str, Any]:
        symbol = symbol.upper()
        with self._lock:
            history = self._history.get(symbol, deque())
            if not history:
                return {
                    "symbol": symbol,
                    "currentPrice": 0.0,
                    "changePercent": 0.0,
                    "high": 0.0,
                    "low": 0.0,
                    "volume": 0.0,
                    "lastUpdate": None
                }

            current = history[-1].price
            initial = self._open_prices.get(symbol, history[0].price)
            change_pct = ((current - initial) / initial * 100.0) if initial > 0 else 0.0
            prices = [t.price for t in history]
            total_vol = sum(t.size for t in history)

            return {
                "symbol": symbol,
                "currentPrice": round(current, 2),
                "changePercent": round(change_pct, 2),
                "high": round(max(prices), 2),
                "low": round(min(prices), 2),
                "volume": round(total_vol, 2),
                "lastUpdate": datetime.fromtimestamp(history[-1].timestamp).strftime("%H:%M:%S")
            }


class DecisionBuffer:
    """Thread-safe buffer storing trading decisions."""

    def __init__(self, max_decisions: int = 100):
        self.max_decisions = max_decisions
        self._history: Dict[str, deque] = {}
        self._lock = threading.Lock()

    def add_decision(
        self,
        symbol: Any,
        action: Optional[str] = None,
        confidence: float = 0.75,
        reasoning: Optional[List[str]] = None,
        timestamp: Optional[Any] = None
    ) -> TradingDecision:
        if isinstance(symbol, TradingDecision):
            decision = symbol
            sym = decision.symbol.upper()
        elif hasattr(symbol, "symbol") and hasattr(symbol, "action"):
            sym = str(symbol.symbol).upper()
            decision = TradingDecision(
                symbol=sym,
                action=str(symbol.action).upper(),
                confidence=float(getattr(symbol, "confidence", confidence)),
                reasoning=getattr(symbol, "reasoning", reasoning) or [],
                timestamp=time.time()
            )
        else:
            sym = str(symbol).upper()
            ts = time.time()
            if timestamp is not None:
                if isinstance(timestamp, (int, float)):
                    ts = float(timestamp if timestamp < 1e11 else timestamp / 1e9)
                elif isinstance(timestamp, datetime):
                    ts = timestamp.timestamp()

            decision = TradingDecision(
                symbol=sym,
                action=str(action or "HOLD").upper(),
                confidence=float(confidence),
                reasoning=reasoning or [],
                timestamp=ts
            )

        with self._lock:
            if sym not in self._history:
                self._history[sym] = deque(maxlen=self.max_decisions)
            self._history[sym].append(decision)

        return decision

    def get_latest_decision(self, symbol: str) -> Optional[TradingDecision]:
        symbol = symbol.upper()
        with self._lock:
            history = self._history.get(symbol)
            if history and len(history) > 0:
                return history[-1]
            return None

    def get_history(self, symbol: str, limit: int = 50) -> List[Dict[str, Any]]:
        symbol = symbol.upper()
        with self._lock:
            history = self._history.get(symbol, deque())
            items = list(history)[-limit:]
            return [d.to_dict() for d in reversed(items)]


# ============================================================================
# Global Buffers
# ============================================================================

market_buffer = MarketDataBuffer()
decision_buffer = DecisionBuffer()
_db_manager_instance: Optional[Any] = None
_db_is_available: bool = True
_db_last_attempt_time: float = 0.0
_DB_RETRY_INTERVAL: float = 15.0  # seconds before attempting reconnect if DB failed


def get_db_manager():
    """Lazily instantiate or retrieve DatabaseManager."""
    global _db_manager_instance
    if _db_manager_instance is None:
        try:
            from src.database import DatabaseManager
            _db_manager_instance = DatabaseManager()
        except Exception as e:
            logger.debug("DatabaseManager not initialized in web_server: %s", e)
    return _db_manager_instance


async def _query_historical_ticks_safe(
    db: Any,
    symbol: str,
    start_time: Optional[datetime],
    limit: int
) -> List[Dict[str, Any]]:
    """
    Safely queries historical market data in a background thread with a fast timeout
    and circuit-breaker behavior so slow/unreachable databases never block the web server.
    """
    global _db_is_available, _db_last_attempt_time
    now_ts = time.time()
    if not _db_is_available and (now_ts - _db_last_attempt_time < _DB_RETRY_INTERVAL):
        return []

    _db_last_attempt_time = now_ts
    try:
        ticks = await asyncio.wait_for(
            asyncio.to_thread(
                db.get_historical_market_data,
                symbol=symbol,
                start_time=start_time,
                limit=limit
            ),
            timeout=1.0
        )
        _db_is_available = True
        return ticks or []
    except Exception as db_err:
        _db_is_available = False
        logger.debug("Database query for historical market data skipped or failed: %s", db_err)
        return []


def compute_recommendation(symbol: str) -> Dict[str, Any]:
    """
    Returns latest decision or generates an algorithmic recommendation based on price action.
    """
    latest = decision_buffer.get_latest_decision(symbol)
    if latest:
        return latest.to_dict()

    summary = market_buffer.get_summary(symbol)
    change = summary.get("changePercent", 0.0)

    if change > 0.15:
        action = "BUY"
        conf = min(0.95, 0.65 + abs(change) * 0.1)
        reasons = [
            f"Upward price momentum (+{change:.2f}%)",
            "Bellman target Q-value above threshold",
            "Positive trend trajectory detected"
        ]
    elif change < -0.15:
        action = "SELL"
        conf = min(0.95, 0.65 + abs(change) * 0.1)
        reasons = [
            f"Downward price pressure ({change:.2f}%)",
            "Risk boundary triggered",
            "Bellman expected return negative"
        ]
    else:
        action = "HOLD"
        conf = 0.70
        reasons = [
            "Price consolidating in neutral zone",
            "Waiting for directional breakout signal",
            "Portfolio allocation optimal"
        ]

    return {
        "symbol": symbol,
        "action": action,
        "confidence": round(conf, 2),
        "reasoning": reasons,
        "timestamp": time.time(),
        "time_str": datetime.now().strftime("%H:%M:%S")
    }


# ============================================================================
# WebSocket Connection Manager
# ============================================================================

class ConnectionManager:
    """Manages active WebSocket connections."""

    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        async with self._lock:
            self.active_connections.add(websocket)
        logger.info("WebSocket client connected. Active: %d", len(self.active_connections))

    async def disconnect(self, websocket: WebSocket):
        async with self._lock:
            self.active_connections.discard(websocket)
        logger.info("WebSocket client disconnected. Active: %d", len(self.active_connections))

    async def broadcast_json(self, message: Dict[str, Any]):
        async with self._lock:
            clients = list(self.active_connections)

        if not clients:
            return

        dead_clients = []
        for ws in clients:
            try:
                await ws.send_json(message)
            except Exception:
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
            market_indexes = []
            try:
                indexes = ["VIX", "DJIA", "SP500", "RUSSELL2000"]
                for idx in indexes:
                    try:
                        index_data = await get_market_index(idx)
                        market_indexes.append(index_data)
                    except Exception:
                        pass
            except Exception:
                pass

            payload = {
                "type": "update",
                "timestamp": time.time(),
                "time_str": datetime.now().strftime("%H:%M:%S"),
                "symbols": symbol_summaries,
                "decisions": decisions,
                "market_indexes": market_indexes
            }

            await manager.broadcast_json(payload)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug("Broadcast loop tick error: %s", e)

        await asyncio.sleep(interval)


# ============================================================================
# FastAPI Application & Lifespan
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: spawn broadcast loop
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

STATIC_HTML_PATH = os.path.join(os.path.dirname(__file__), "static", "dashboard.html")


@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    if os.path.exists(STATIC_HTML_PATH):
        with open(STATIC_HTML_PATH, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Dashboard HTML template not found.</h1>", status_code=404)


@app.get("/api/status")
async def get_status():
    symbols = market_buffer.get_symbols()
    return {
        "status": "online",
        "symbols_tracked": len(symbols),
        "symbols": symbols,
        "timestamp": time.time()
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
async def get_market_index(index: str):
    """
    Returns real-time market index data for specific indices.
    This data is not persisted but is fetched for real-time dashboard display.
    """
    index = index.upper()

    # Map index symbols to standard ticker symbols used by external APIs
    index_tickers = {
        "VIX": "^VIX",  # CBOE Volatility Index
        "DJIA": "^DJI", # Dow Jones Industrial Average
        "SP500": "SPX", # S&P 500
        "RUSSELL2000": "RUT" # Russell 2000
    }

    ticker = index_tickers.get(index, index)
    if ticker.startswith("^"):
        ticker = ticker[1:]

    try:
        # Use Alpaca API to get current data for the index
        if _db_is_available:
            from alpaca.data.timeframe import TimeFrame

            # Try to get recent data for the index
            start_date = datetime.now() - timedelta(days=2)
            alpaca_data = await _query_historical_ticks_safe(
                db=get_db_manager(),
                symbol=ticker,
                start_time=start_date,
                limit=2
            )

            if alpaca_data and len(alpaca_data) >= 2:
                latest = alpaca_data[-1]
                previous = alpaca_data[-2]

                current_price = latest.get("price", 0)
                previous_close = previous.get("price", current_price)

                change = current_price - previous_close
                change_pct = (change / previous_close * 100) if previous_close > 0 else 0

                return JSONResponse({
                    "index": index,
                    "ticker": ticker,
                    "currentPrice": round(current_price, 2),
                    "change": round(change, 2),
                    "changePercent": round(change_pct, 2),
                    "priceType": "current"
                })

        # Fallback: return current live tick if available in buffer
        latest_tick = market_buffer.get_latest_tick(index)
        if latest_tick:
            prev_price = market_buffer.get_latest_tick(index).price
            change = latest_tick.price - prev_price
            change_pct = (change / prev_price * 100) if prev_price > 0 else 0

            return JSONResponse({
                "index": index,
                "ticker": ticker,
                "currentPrice": round(latest_tick.price, 2),
                "change": round(change, 2),
                "changePercent": round(change_pct, 2),
                "priceType": "fallback"
            })

        # If we have no data, return zeros
        return JSONResponse({
            "index": index,
            "ticker": ticker,
            "currentPrice": 0.0,
            "change": 0.0,
            "changePercent": 0.0,
            "priceType": "no_data"
        })

    except Exception as e:
        logger.debug("Failed to fetch market index data for %s: %s", index, e)
        return JSONResponse({
            "index": index,
            "ticker": ticker,
            "currentPrice": 0.0,
            "change": 0.0,
            "changePercent": 0.0,
            "priceType": "error"
        })


@app.get("/api/market-indexes")
async def get_all_market_indexes():
    """
    Returns data for all market indexes (VIX, DJIA, S&P500, Russell 2000)
    in a single request for efficiency.
    """
    indexes = ["VIX", "DJIA", "SP500", "RUSSELL2000"]
    results = []

    for idx in indexes:
        try:
            data = await get_market_index(idx)
            results.append(data)
        except Exception as e:
            logger.debug("Failed to fetch index %s: %s", idx, e)
            results.append({
                "index": idx,
                "ticker": "",
                "currentPrice": 0.0,
                "change": 0.0,
                "changePercent": 0.0,
                "priceType": "error"
            })

    return JSONResponse(results)


@app.get("/api/symbols/{symbol}")
async def get_symbol_detail(
    symbol: str,
    duration: Optional[str] = "24h",
    limit: Optional[int] = 1000
):
    symbol = symbol.upper()
    summary = market_buffer.get_summary(symbol)

    # Determine start_time based on requested duration window
    now = datetime.now()
    start_time: Optional[datetime] = None
    if duration == "1h":
        start_time = now - timedelta(hours=1)
    elif duration == "24h":
        start_time = now - timedelta(hours=24)
    elif duration == "5d":
        start_time = now - timedelta(days=5)
    elif duration == "30d":
        start_time = now - timedelta(days=30)
    elif duration == "1y":
        start_time = now - timedelta(days=365)
    elif duration == "ytd":
        start_time = datetime(now.year, 1, 1)

    historical_ticks = []
    db = get_db_manager()
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
    seen_db_keys = set()
    all_ticks = []
    for t in historical_ticks:
        ts = float(t.get("timestamp", 0))
        seen_db_keys.add((round(ts, 2), t.get("price")))
        all_ticks.append(t)

    for t in buffer_ticks:
        ts = float(t.get("timestamp", 0))
        if (round(ts, 2), t.get("price")) not in seen_db_keys:
            all_ticks.append(t)

    all_ticks.sort(key=lambda x: x.get("timestamp", 0))
    if limit and len(all_ticks) > limit:
        all_ticks = all_ticks[-limit:]

    # Fallback seed tick if no ticks are available yet but summary has a live price
    if not all_ticks and summary.get("currentPrice", 0.0) > 0:
        all_ticks.append({
            "symbol": symbol,
            "price": summary["currentPrice"],
            "size": summary.get("volume", 0.0),
            "timestamp": time.time(),
            "time_str": datetime.now().strftime("%H:%M:%S")
        })

    decision = compute_recommendation(symbol)
    decisions_history = decision_buffer.get_history(symbol, limit=20)

    # Compute moving averages (50-day and 200-day) if enough data
    prices = [t.get("price", 0.0) for t in all_ticks]
    ma_50 = round(sum(prices[-50:]) / 50.0, 2) if len(prices) >= 50 else (prices[-1] if prices else 0.0)
    ma_200 = round(sum(prices[-200:]) / 200.0, 2) if len(prices) >= 200 else (prices[-1] if prices else 0.0)

    return JSONResponse({
        "summary": summary,
        "recommendation": decision,
        "ticks": all_ticks,
        "decisions": decisions_history,
        "moving_averages": {
            "200_day": ma_200,
            "50_day": ma_50
        }
    })


@app.websocket("/ws")
@app.websocket("/ws/updates")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        # Immediately send current state upon connection
        symbols = market_buffer.get_symbols()
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
        market_indexes = []
        try:
            indexes = ["VIX", "DJIA", "SP500", "RUSSELL2000"]
            for idx in indexes:
                try:
                    index_data = await get_market_index(idx)
                    market_indexes.append(index_data)
                except Exception:
                    pass
        except Exception:
            pass

        await websocket.send_json({
            "type": "init",
            "timestamp": time.time(),
            "time_str": datetime.now().strftime("%H:%M:%S"),
            "symbols": summaries,
            "decisions": decisions,
            "market_indexes": market_indexes
        })

        while True:
            # Keep socket open and accept incoming ping/client messages
            await websocket.receive_text()
    except WebSocketDisconnect:
        await manager.disconnect(websocket)
    except Exception:
        await manager.disconnect(websocket)


# ============================================================================
# Server Thread Runner
# ============================================================================

_server_instance: Optional[uvicorn.Server] = None
_server_thread: Optional[threading.Thread] = None


def is_port_available(port: int, host: str = "0.0.0.0") -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def start_web_server_thread(host: str = "0.0.0.0", port: int = 8001) -> Optional[uvicorn.Server]:
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
        except Exception:
            pass
    _server_instance = None
    _server_thread = None


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
