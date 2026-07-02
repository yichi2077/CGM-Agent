from __future__ import annotations

import csv
import importlib.util
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

from hermes_cgm_agent.domain import DataScope
from hermes_cgm_agent.services.audit import AuditService
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.sources import SourcePollConfig, SourcePollService
from hermes_cgm_agent.services.tools import ToolExecutor
from hermes_cgm_agent.storage.sqlite import SQLiteStore


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_example_module(name: str, relative_path: str):
    path = PROJECT_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


dataset = _load_example_module(
    "test_generate_cgm_dataset",
    "examples/cgm_test_dataset/generate_cgm_dataset.py",
)
feed = _load_example_module(
    "test_virtual_cgm_feed",
    "examples/cgm_test_dataset/virtual_cgm_feed.py",
)
auto_poll = _load_example_module(
    "test_auto_poll",
    "examples/cgm_test_dataset/auto_poll.py",
)
simulation_tick = _load_example_module(
    "test_simulation_tick",
    "examples/cgm_test_dataset/simulation_tick.py",
)


class VirtualCGMDatasetTests(unittest.TestCase):
    def test_default_generator_outputs_smooth_one_minute_prediabetes_fixture(self) -> None:
        rows, events, artifacts = dataset.generate(
            start=datetime(2026, 4, 25, 0, 0),
            days=14,
            interval_min=1,
            seed=20260701,
        )
        timestamps = [datetime.fromisoformat(row["timestamp"]) for row in rows]
        values = [float(row["value"]) for row in rows]
        one_minute_steps = [
            abs(b - a)
            for a, b, before, after in zip(values, values[1:], timestamps, timestamps[1:])
            if (after - before) == timedelta(minutes=1)
        ]

        self.assertEqual(rows[0]["timestamp"], "2026-04-25T02:00:00")
        self.assertEqual(len(rows), 20010)
        self.assertEqual(len(events), 55)
        self.assertEqual({item["type"] for item in artifacts}, {"compression_low", "dropout", "sensor_noise"})
        self.assertLessEqual(max(one_minute_steps), 3.81)
        self.assertGreater(max(values), 180)
        self.assertLess(min(values), 80)
        self.assertGreater(sum(70 <= value <= 180 for value in values) / len(values), 0.86)

    def test_virtual_http_feed_source_poll_to_hermes_tools(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            rows, _, _ = dataset.generate(
                start=datetime(2026, 4, 25, 0, 0),
                days=1,
                interval_min=1,
                seed=20260701,
            )
            csv_path = temp_path / "cgm_14d_1min.csv"
            with csv_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)

            state = feed.VirtualCGMFeedState(
                feed.load_points(csv_path),
                timezone_name="Asia/Shanghai",
                emit_interval_minutes=5,
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), feed.build_handler(state))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                store = SQLiteStore(temp_path / "app.db")
                store.initialize()
                repository = SQLiteCGMRepository(store)
                service = SourcePollService(
                    repository=repository,
                    config=SourcePollConfig(expected_interval_minutes=5),
                )
                url = f"http://127.0.0.1:{server.server_port}"
                first = service.poll(
                    user_id="user-1",
                    kind="xdrip",
                    url=url,
                    count=1,
                    source="virtual:aidex",
                    received_at=datetime(2026, 4, 24, 18, 5, tzinfo=timezone.utc),
                )
                second = service.poll(
                    user_id="user-1",
                    kind="xdrip",
                    url=url,
                    count=1,
                    source="virtual:aidex",
                    received_at=datetime(2026, 4, 24, 18, 10, tzinfo=timezone.utc),
                )

                scope = DataScope(
                    user_id="user-1",
                    window_start=datetime(2026, 4, 24, 18, 0, tzinfo=timezone.utc),
                    window_end=datetime(2026, 4, 24, 18, 10, tzinfo=timezone.utc),
                    source="virtual:aidex",
                )
                points = repository.list_glucose_points(scope)
                executor = ToolExecutor(repository=repository, audit_service=AuditService(store))
                aggregate = executor.execute(
                    tool_name="timeseries.get_aggregate",
                    session_id="virtual-feed-test",
                    arguments={
                        "data_scope": scope.model_dump(mode="json"),
                        "window_label": "day",
                        "expected_interval_minutes": 5,
                    },
                ).to_dict()
                realtime = executor.execute(
                    tool_name="timeseries.get_realtime_snapshot",
                    session_id="virtual-feed-test",
                    arguments={
                        "data_scope": scope.model_dump(mode="json"),
                        "expected_interval_minutes": 5,
                        "now": "2026-04-24T18:05:00+00:00",
                    },
                ).to_dict()

                self.assertEqual(first.inserted_count, 1)
                self.assertEqual(second.inserted_count, 1)
                self.assertEqual(len(points), 2)
                self.assertEqual(points[1].timestamp - points[0].timestamp, timedelta(minutes=5))
                self.assertEqual(aggregate["status"], "ok")
                self.assertEqual(aggregate["aggregate"]["point_count"], 2)
                self.assertEqual(aggregate["aggregate"]["data_coverage"], 100.0)
                self.assertEqual(realtime["status"], "ok")
                self.assertIsNotNone(realtime["snapshot"]["latest_glucose_mg_dl"])
            finally:
                server.shutdown()
                server.server_close()

    def test_virtual_http_feed_poll_to_daily_report_e2e(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            rows, _, _ = dataset.generate(
                start=datetime(2026, 4, 25, 0, 0),
                days=1,
                interval_min=1,
                seed=20260701,
            )
            csv_path = temp_path / "cgm_14d_1min.csv"
            with csv_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)

            state = feed.VirtualCGMFeedState(
                feed.load_points(csv_path),
                timezone_name="Asia/Shanghai",
                emit_interval_minutes=5,
            )
            expected_points = len(state.points)
            server = ThreadingHTTPServer(("127.0.0.1", 0), feed.build_handler(state))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                store = SQLiteStore(temp_path / "app.db")
                store.initialize()
                repository = SQLiteCGMRepository(store)
                service = SourcePollService(
                    repository=repository,
                    config=SourcePollConfig(expected_interval_minutes=5),
                )
                url = f"http://127.0.0.1:{server.server_port}"
                poll = service.poll(
                    user_id="user-1",
                    kind="xdrip",
                    url=url,
                    count=288,
                    source="virtual:aidex",
                    received_at=datetime(2026, 4, 25, 18, 0, tzinfo=timezone.utc),
                )

                scope = DataScope(
                    user_id="user-1",
                    window_start=datetime(2026, 4, 24, 18, 0, tzinfo=timezone.utc),
                    window_end=datetime(2026, 4, 25, 18, 0, tzinfo=timezone.utc),
                    source="virtual:aidex",
                )
                executor = ToolExecutor(repository=repository, audit_service=AuditService(store))
                aggregate = executor.execute(
                    tool_name="timeseries.get_aggregate",
                    session_id="virtual-report-e2e",
                    arguments={
                        "data_scope": scope.model_dump(mode="json"),
                        "window_label": "day",
                        "expected_interval_minutes": 5,
                    },
                ).to_dict()
                report = executor.execute(
                    tool_name="reports.generate",
                    session_id="virtual-report-e2e",
                    arguments={
                        "user_id": "user-1",
                        "report_type": "daily",
                        "data_scope": scope.model_dump(mode="json"),
                    },
                ).to_dict()
                section_ids = {section["section_id"] for section in report["sections"]}

                self.assertEqual(poll.inserted_count, expected_points)
                self.assertEqual(aggregate["status"], "ok")
                self.assertEqual(aggregate["aggregate"]["point_count"], expected_points)
                self.assertEqual(report["status"], "ok")
                self.assertEqual(report["report"]["report_type"], "daily")
                self.assertIn("overview", section_ids)
                self.assertIn("metrics", section_ids)
                self.assertTrue(report["evidence_refs"])
                self.assertTrue(report["rendered_markdown"].strip())
                self.assertNotIn("no_valid_points", report["rendered_markdown"])
            finally:
                server.shutdown()
                server.server_close()

    def test_auto_poll_runner_advances_virtual_feed_into_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            rows, _, _ = dataset.generate(
                start=datetime(2026, 4, 25, 0, 0),
                days=1,
                interval_min=1,
                seed=20260701,
            )
            csv_path = temp_path / "cgm_14d_1min.csv"
            with csv_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)

            state = feed.VirtualCGMFeedState(
                feed.load_points(csv_path),
                timezone_name="Asia/Shanghai",
                emit_interval_minutes=5,
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), feed.build_handler(state))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                db_path = temp_path / "app.db"
                summary = auto_poll.run_auto_poll(
                    db_path=db_path,
                    user_id="user-1",
                    kind="xdrip",
                    url=f"http://127.0.0.1:{server.server_port}",
                    count=2,
                    source="virtual:aidex",
                    expected_interval_minutes=5,
                    interval_seconds=0,
                    max_polls=2,
                    emit_json=False,
                )
                store = SQLiteStore(db_path)
                store.initialize()
                repository = SQLiteCGMRepository(store)
                scope = DataScope(
                    user_id="user-1",
                    window_start=datetime(2026, 4, 24, 18, 0, tzinfo=timezone.utc),
                    window_end=datetime(2026, 4, 24, 18, 20, tzinfo=timezone.utc),
                    source="virtual:aidex",
                )
                points = repository.list_glucose_points(scope)

                self.assertEqual(summary.status, "ok")
                self.assertEqual(summary.poll_count, 2)
                self.assertEqual(summary.inserted_count, 4)
                self.assertEqual(len(points), 4)
                self.assertEqual(points[-1].timestamp - points[0].timestamp, timedelta(minutes=15))
            finally:
                server.shutdown()
                server.server_close()

    def test_simulation_tick_resumes_from_sqlite_point_count(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            rows, _, _ = dataset.generate(
                start=datetime(2026, 4, 25, 0, 0),
                days=1,
                interval_min=1,
                seed=20260701,
            )
            csv_path = temp_path / "cgm_14d_1min.csv"
            with csv_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)

            db_path = temp_path / "app.db"
            first = simulation_tick.run_simulation_tick(
                db_path=db_path,
                user_id="user-1",
                kind="xdrip",
                source="virtual:aidex",
                csv_path=csv_path,
                timezone_name="Asia/Shanghai",
                emit_interval_minutes=5,
                expected_interval_minutes=5,
                received_at=datetime(2026, 4, 24, 18, 5, tzinfo=timezone.utc),
            )
            second = simulation_tick.run_simulation_tick(
                db_path=db_path,
                user_id="user-1",
                kind="xdrip",
                source="virtual:aidex",
                csv_path=csv_path,
                timezone_name="Asia/Shanghai",
                emit_interval_minutes=5,
                expected_interval_minutes=5,
                received_at=datetime(2026, 4, 24, 18, 10, tzinfo=timezone.utc),
            )

            store = SQLiteStore(db_path)
            store.initialize()
            repository = SQLiteCGMRepository(store)
            scope = DataScope(
                user_id="user-1",
                window_start=datetime(2026, 4, 24, 18, 0, tzinfo=timezone.utc),
                window_end=datetime(2026, 4, 24, 18, 15, tzinfo=timezone.utc),
                source="virtual:aidex",
            )
            points = repository.list_glucose_points(scope)

            self.assertEqual(first.status, "ok")
            self.assertEqual(first.start_index, 0)
            self.assertEqual(first.inserted_count, 1)
            self.assertEqual(second.status, "ok")
            self.assertEqual(second.start_index, 1)
            self.assertEqual(second.inserted_count, 1)
            self.assertEqual(len(points), 2)
            self.assertEqual(points[1].timestamp - points[0].timestamp, timedelta(minutes=5))


if __name__ == "__main__":
    unittest.main()
