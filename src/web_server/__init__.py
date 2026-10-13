"""The web server and live dashboard, as three layers.

  :mod:`src.web_server.data_layer`  the buffers, the index store and the
                                  database access the dashboard reads
  :mod:`src.web_server.api_layer`   the FastAPI routes and the WebSocket
                                  endpoint

The templates themselves are static assets in ``src/static/``. This module
re-exports the public surface so ``src.web_server`` keeps the names the
pipeline, the scripts and ``python -m uvicorn src.web_server:app`` use.
"""

from src.web_server.api_layer import (
    ConnectionManager,
    app,
    get_all_market_indexes,
    get_market_index,
    is_port_available,
    manager,
    start_web_server_thread,
    stop_web_server,
)
from src.web_server.data_layer import (
    TRACKED_INDEXES,
    DecisionBuffer,
    IndexStore,
    MarketDataBuffer,
    MarketTick,
    TradingDecision,
    compute_recommendation,
    decision_buffer,
    get_db_manager,
    index_store,
    market_buffer,
    update_index_from_tick,
)

# Re-exports, not new names: the pipeline imports ``from src.web_server import
# market_buffer`` and uvicorn resolves ``src.web_server:app``, so both keep
# working against the split modules. Listing them here is what tells ruff the
# imports are a public surface rather than unused aliases.
__all__ = [
    "ConnectionManager",
    "DecisionBuffer",
    "IndexStore",
    "MarketDataBuffer",
    "MarketTick",
    "TRACKED_INDEXES",
    "TradingDecision",
    "app",
    "compute_recommendation",
    "decision_buffer",
    "get_all_market_indexes",
    "get_db_manager",
    "get_market_index",
    "index_store",
    "is_port_available",
    "manager",
    "market_buffer",
    "start_web_server_thread",
    "stop_web_server",
    "update_index_from_tick",
]
