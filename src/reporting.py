import os
import csv
import json
import uuid
import logging
from typing import List, Dict, Any
from datetime import datetime

logger = logging.getLogger("Reporting")


def get_dated_output_path(filename: str, base_dir: str = "outputs") -> str:
    """Returns a file path inside a date-stamped folder under base_dir (per README spec)."""
    date_str = datetime.now().strftime("%Y-%m-%d")
    folder = os.path.join(base_dir, date_str)
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, filename)


class CSVReporter:
    """Generates structured CSV reports from trade logs and portfolio snapshots."""

    def generate_report(self, trading_data: List[Dict[str, Any]], output_path: str) -> str:
        if not trading_data:
            os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
            with open(output_path, "w", newline="") as f:
                f.write("")
            return output_path

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        fieldnames = list(trading_data[0].keys())

        with open(output_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in trading_data:
                cleaned_row = {}
                for k, v in row.items():
                    if isinstance(v, datetime):
                        cleaned_row[k] = v.isoformat()
                    else:
                        cleaned_row[k] = v
                writer.writerow(cleaned_row)

        logger.info(f"Report successfully saved to {output_path}")
        return output_path


class ChartGenerator:
    """Creates visual portfolio reports using HTML/Plotly."""

    def create_portfolio_chart(
        self,
        portfolio_history: List[Dict[str, Any]],
        output_path: str
    ) -> str:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

        try:
            import plotly.graph_objects as go
            timestamps = [str(p.get("timestamp", "")) for p in portfolio_history]
            values = [p.get("value", 0.0) for p in portfolio_history]
            benchmarks = [p.get("benchmark", 0.0) for p in portfolio_history]

            fig = go.Figure()
            fig.add_trace(go.Scatter(x=timestamps, y=values, mode="lines+markers", name="Portfolio Value"))
            if any(benchmarks):
                fig.add_trace(go.Scatter(x=timestamps, y=benchmarks, mode="lines", name="Benchmark"))

            fig.update_layout(
                title="Portfolio Performance",
                xaxis_title="Timestamp",
                yaxis_title="Value ($)"
            )
            fig.write_html(output_path)
        except ImportError:
            # Fallback HTML with Plotly JS embed
            history_json = json.dumps(portfolio_history, default=str)
            html_content = f"""<!DOCTYPE html>
<html>
<head>
    <title>Portfolio Performance</title>
    <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
</head>
<body>
    <div id="plotly-chart"></div>
    <script>
        var historyData = {history_json};
        var trace1 = {{
            x: historyData.map(h => h.timestamp),
            y: historyData.map(h => h.value),
            type: 'scatter',
            name: 'Portfolio Value'
        }};
        Plotly.newPlot('plotly-chart', [trace1], {{title: 'Portfolio Performance'}});
    </script>
</body>
</html>"""
            with open(output_path, "w") as f:
                f.write(html_content)

        logger.info(f"Chart generated at {output_path}")
        return output_path


class PerformanceAnalyzer:
    """Calculates risk, return, and performance metrics from executed trades."""

    def calculate_metrics(self, trades: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not trades:
            return {
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "win_rate": 0.0,
                "total_return": 0.0,
                "profit_factor": 0.0,
                "avg_win": 0.0,
                "avg_loss": 0.0
            }

        total_return = 0.0
        wins = []
        losses = []

        for t in trades:
            # Can be defined with entry/exit price and qty, or direct pnl/total
            if "entry_price" in t and "exit_price" in t and "quantity" in t:
                pnl = (t["exit_price"] - t["entry_price"]) * t["quantity"]
            elif "pnl" in t:
                pnl = float(t["pnl"])
            elif "entry" in t and "exit" in t and "qty" in t:
                pnl = (t["exit"] - t["entry"]) * t["qty"]
            else:
                pnl = 0.0

            total_return += pnl
            if pnl > 0:
                wins.append(pnl)
            elif pnl < 0:
                losses.append(abs(pnl))

        total_trades = len(trades)
        winning_trades = len(wins)
        losing_trades = len(losses)
        win_rate = winning_trades / total_trades if total_trades > 0 else 0.0
        total_loss = sum(losses)
        raw_profit_factor = (sum(wins) / total_loss) if total_loss > 0 else (float("inf") if wins else 0.0)
        profit_factor = round(raw_profit_factor, 2) if raw_profit_factor != float("inf") else float("inf")

        return {
            "total_trades": total_trades,
            "winning_trades": winning_trades,
            "losing_trades": losing_trades,
            "win_rate": win_rate,
            "total_return": round(total_return, 2),
            "profit_factor": profit_factor,
            "avg_win": round(sum(wins) / len(wins), 2) if wins else 0.0,
            "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0.0
        }


class ReportScheduler:
    """Manages scheduled automated report generation."""

    def __init__(self):
        self._jobs: Dict[str, Dict[str, Any]] = {}

    def schedule_report(
        self,
        report_type: str,
        interval_hours: int,
        output_path: str
    ) -> str:
        job_id = f"job-{uuid.uuid4().hex[:8]}"
        self._jobs[job_id] = {
            "job_id": job_id,
            "report_type": report_type,
            "interval_hours": interval_hours,
            "output_path": output_path,
            "scheduled_at": datetime.now(),
            "status": "scheduled"
        }
        return job_id

    def get_scheduled_jobs(self) -> Dict[str, Dict[str, Any]]:
        return dict(self._jobs)

    def cancel_job(self, job_id: str) -> bool:
        if job_id in self._jobs:
            del self._jobs[job_id]
            return True
        return False


class MultiSymbolReporter:
    """Generates cross-symbol aggregated performance reports."""

    def generate_report(
        self,
        symbol_data: Dict[str, List[Dict[str, Any]]],
        output_path: str
    ) -> str:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        html = [
            "<!DOCTYPE html><html><head><title>Multi-Symbol Performance</title></head><body>",
            "<h1>Multi-Symbol Performance Report</h1>",
            "<table border='1'><tr><th>Symbol</th><th>Data Points</th><th>Latest Value</th></tr>"
        ]
        for symbol, data_points in symbol_data.items():
            latest = data_points[-1].get("value", "N/A") if data_points else "N/A"
            html.append(f"<tr><td>{symbol}</td><td>{len(data_points)}</td><td>{latest}</td></tr>")
        html.append("</table></body></html>")

        with open(output_path, "w") as f:
            f.write("\n".join(html))

        return output_path
