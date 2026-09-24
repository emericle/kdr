import pytest
import os
import tempfile
from datetime import datetime
from unittest.mock import MagicMock


class TestCSVReporter:
    """Unit tests for CSVReporter."""

    def test_generate_report_creates_csv(self):
        from src.reporting import CSVReporter

        reporter = CSVReporter()
        data = [
            {
                "timestamp": datetime(2023, 1, 1, 9, 30),
                "symbol": "AAPL",
                "action": "buy",
                "quantity": 100,
                "price": 150.0,
                "total": 15000.0,
                "portfolio_value": 15000.0
            },
            {
                "timestamp": datetime(2023, 1, 1, 12, 0),
                "symbol": "AAPL",
                "action": "sell",
                "quantity": 100,
                "price": 155.0,
                "total": 15500.0,
                "portfolio_value": 15500.0
            }
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = os.path.join(tmpdir, "report.csv")
            result_path = reporter.generate_report(data, out_file)
            assert os.path.exists(result_path)

            with open(result_path, "r") as f:
                content = f.read()
            assert "symbol" in content
            assert "AAPL" in content
            assert "155.0" in content


class TestChartGenerator:
    """Unit tests for ChartGenerator."""

    def test_create_portfolio_chart(self):
        from src.reporting import ChartGenerator

        generator = ChartGenerator()
        history = [
            {"timestamp": datetime(2023, 1, 1), "value": 10000.0, "benchmark": 10000.0},
            {"timestamp": datetime(2023, 1, 2), "value": 10500.0, "benchmark": 10100.0}
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = os.path.join(tmpdir, "chart.html")
            path = generator.create_portfolio_chart(history, out_file)
            assert os.path.exists(path)

            with open(path, "r") as f:
                html = f.read()
            assert "plotly" in html.lower()


class TestPerformanceAnalyzer:
    """Unit tests for PerformanceAnalyzer."""

    def test_calculate_metrics(self):
        from src.reporting import PerformanceAnalyzer

        analyzer = PerformanceAnalyzer()
        trades = [
            {"entry_price": 100.0, "exit_price": 110.0, "quantity": 10},  # +$100
            {"entry_price": 50.0, "exit_price": 45.0, "quantity": 10},   # -$50
            {"entry_price": 200.0, "exit_price": 220.0, "quantity": 5}   # +$100
        ]
        metrics = analyzer.calculate_metrics(trades)
        assert metrics["total_trades"] == 3
        assert metrics["winning_trades"] == 2
        assert metrics["losing_trades"] == 1
        assert metrics["win_rate"] == pytest.approx(2.0 / 3.0)
        assert metrics["total_return"] == pytest.approx(150.0)


class TestReportScheduler:
    """Unit tests for ReportScheduler."""

    def test_schedule_and_cancel_report(self):
        from src.reporting import ReportScheduler

        scheduler = ReportScheduler()
        job_id = scheduler.schedule_report(
            report_type="portfolio",
            interval_hours=1,
            output_path="/tmp/test_report.html"
        )
        assert isinstance(job_id, str)
        assert len(job_id) > 0
        jobs = scheduler.get_scheduled_jobs()
        assert job_id in jobs

        assert scheduler.cancel_job(job_id) is True
        assert job_id not in scheduler.get_scheduled_jobs()


class TestMultiSymbolReporter:
    """Unit tests for MultiSymbolReporter."""

    def test_generate_report(self):
        from src.reporting import MultiSymbolReporter

        reporter = MultiSymbolReporter()
        data = {
            "AAPL": [{"timestamp": datetime(2023, 1, 1), "value": 10000.0}],
            "MSFT": [{"timestamp": datetime(2023, 1, 1), "value": 12000.0}]
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = os.path.join(tmpdir, "multi_report.html")
            path = reporter.generate_report(data, out_file)
            assert os.path.exists(path)


class TestReportingCoverageAdditions:
    def test_get_dated_output_path(self):
        from src.reporting import get_dated_output_path
        with tempfile.TemporaryDirectory() as tmpdir:
            p = get_dated_output_path("test.csv", base_dir=tmpdir)
            assert os.path.basename(p) == "test.csv"
            assert tmpdir in p

    def test_csv_reporter_empty_data(self):
        from src.reporting import CSVReporter
        reporter = CSVReporter()
        with tempfile.TemporaryDirectory() as tmpdir:
            out = os.path.join(tmpdir, "empty.csv")
            res = reporter.generate_report([], out)
            assert os.path.exists(res)

    def test_performance_analyzer_empty_and_edge_cases(self):
        from src.reporting import PerformanceAnalyzer
        pa = PerformanceAnalyzer()
        assert pa.calculate_metrics([])["total_trades"] == 0

        # All wins (losing trades == 0, profit_factor == inf)
        wins = [{"entry_price": 100.0, "exit_price": 110.0, "quantity": 10}]
        res_wins = pa.calculate_metrics(wins)
        assert res_wins["profit_factor"] == float("inf")

        # All losses (gross_profits == 0)
        losses = [{"entry_price": 100.0, "exit_price": 90.0, "quantity": 10}]
        res_losses = pa.calculate_metrics(losses)
        assert res_losses["profit_factor"] == 0.0

        # Alternate trade formats
        alt_trades = [
            {"pnl": 50.0},
            {"entry": 100.0, "exit": 120.0, "qty": 2},
            {"other": 123}
        ]
        res_alt = pa.calculate_metrics(alt_trades)
        assert res_alt["total_trades"] == 3
        assert res_alt["total_return"] == 90.0

    def test_report_scheduler_cancel_missing(self):
        from src.reporting import ReportScheduler
        rs = ReportScheduler()
        assert not rs.cancel_job("nonexistent-job-id")

    def test_chart_generator_plotly_importerror_fallback(self):
        import sys
        from unittest.mock import patch
        from src.reporting import ChartGenerator

        cg = ChartGenerator()
        with tempfile.TemporaryDirectory() as tmpdir:
            out = os.path.join(tmpdir, "fallback.html")
            # Patch plotly.graph_objects in sys.modules to simulate missing plotly
            with patch.dict(sys.modules, {"plotly.graph_objects": None}):
                path = cg.create_portfolio_chart([{"timestamp": "2023-01-01", "value": 100}], out)
                assert os.path.exists(path)
                with open(path) as f:
                    assert "cdn.plot.ly" in f.read()
