import threading

from battery_bank.acquisition.snapshots import PackInfo
from battery_bank.acquisition.workers import DISCOVERY_RETRY_SECONDS, BatteryPortWorker, SerialWorker, ShuntWorker
from battery_bank.config import BatteryPortConfig
from tests.factories import make_snapshot

PORT_CONFIG = BatteryPortConfig(device="/dev/ttyUSB0", pack_addresses=(1, 2), require_direct_connection=False)


def make_info(unique_id):
    return PackInfo(unique_id=unique_id, hardware_description="JBD UP 4S", production_description=None)


class FakeClock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


class FakePackPoller:
    """Mirrors the PackPoller surface a BatteryPortWorker drives."""

    def __init__(self):
        self.discovered: dict[int, PackInfo] = {}
        self.discover_calls = 0
        self.poll_results: list[list] = []
        self.resettable: set[str] = set()
        self.accepted_resets: list[tuple[str, float]] = []
        self.refuse_resets = False

    def discover(self):
        self.discover_calls += 1
        return dict(self.discovered)

    def poll(self):
        return self.poll_results.pop(0) if self.poll_results else []

    def resettable_pack_ids(self):
        return frozenset(self.resettable)

    def request_soc_reset(self, unique_id, soc_percent):
        if self.refuse_resets:
            return False
        self.accepted_resets.append((unique_id, soc_percent))
        return True


def make_worker(poller=None, clock=None):
    return BatteryPortWorker(PORT_CONFIG, poller if poller is not None else FakePackPoller(), 1.0, clock or FakeClock())


class TestBatteryPortWorker:
    def test_pass_discovers_then_polls_and_exports_results(self):
        poller = FakePackPoller()
        poller.discovered = {1: make_info("pack-1")}
        poller.poll_results = [[make_snapshot(unique_id="pack-1")]]
        worker = make_worker(poller)
        worker._pass_once()
        assert set(worker.infos()) == {"pack-1"}
        assert set(worker.snapshots()) == {"pack-1"}

    def test_discovery_is_retried_only_after_the_retry_interval(self):
        poller = FakePackPoller()
        poller.discovered = {1: make_info("pack-1")}  # one of the two configured packs stays missing
        clock = FakeClock()
        worker = make_worker(poller, clock)
        worker._pass_once()
        clock.now += 1.0
        worker._pass_once()
        assert poller.discover_calls == 1
        clock.now += DISCOVERY_RETRY_SECONDS
        worker._pass_once()
        assert poller.discover_calls == 2

    def test_discovery_stops_once_all_packs_are_found(self):
        poller = FakePackPoller()
        poller.discovered = {1: make_info("pack-1"), 2: make_info("pack-2")}
        clock = FakeClock()
        worker = make_worker(poller, clock)
        worker._pass_once()
        clock.now += DISCOVERY_RETRY_SECONDS * 2
        worker._pass_once()
        assert poller.discover_calls == 1

    def test_pack_missing_a_pass_keeps_its_last_snapshot(self):
        poller = FakePackPoller()
        poller.poll_results = [[make_snapshot(unique_id="pack-1", soc_percent=80.0)], []]
        worker = make_worker(poller)
        worker._pass_once()
        worker._pass_once()
        assert worker.snapshots()["pack-1"].soc_percent == 80.0

    def test_soc_reset_is_forwarded_on_the_next_pass(self):
        poller = FakePackPoller()
        poller.resettable = {"pack-1"}
        worker = make_worker(poller)
        worker._pass_once()  # exports the resettable view
        assert worker.request_soc_reset("pack-1", 100.0) is True
        worker._pass_once()
        assert poller.accepted_resets == [("pack-1", 100.0)]

    def test_soc_reset_refused_for_unknown_pack_or_invalid_percent(self):
        poller = FakePackPoller()
        poller.resettable = {"pack-1"}
        worker = make_worker(poller)
        worker._pass_once()
        assert worker.request_soc_reset("pack-1", 101.0) is False
        assert worker.request_soc_reset("pack-9", 100.0) is False
        worker._pass_once()
        assert poller.accepted_resets == []

    def test_reset_refused_at_apply_time_is_logged_not_raised(self, caplog):
        poller = FakePackPoller()
        poller.resettable = {"pack-1"}
        worker = make_worker(poller)
        worker._pass_once()
        assert worker.request_soc_reset("pack-1", 100.0) is True
        poller.refuse_resets = True
        worker._pass_once()
        assert "SoC reset refused" in caplog.text

    def test_pass_exceptions_are_counted_and_reset_on_success(self, caplog):
        poller = FakePackPoller()
        worker = make_worker(poller)
        original_poll = poller.poll
        poller.poll = lambda: 1 / 0
        worker._tick()
        worker._tick()
        assert worker.consecutive_failures() == 2
        assert "pass failed" in caplog.text
        poller.poll = original_poll
        worker._tick()
        assert worker.consecutive_failures() == 0


class TestShuntWorker:
    def test_pass_exports_the_latest_snapshot(self):
        class FakeShuntPoller:
            def poll(self):
                return "snapshot"

        worker = ShuntWorker(FakeShuntPoller(), 1.0)
        assert worker.snapshot() is None
        worker._pass_once()
        assert worker.snapshot() == "snapshot"


class TestWorkerThread:
    def test_start_and_stop_run_passes_on_the_thread(self):
        passed = threading.Event()

        class OnePassWorker(SerialWorker):
            def _pass_once(self):
                passed.set()

        worker = OnePassWorker("test-worker", 0.01)
        worker.start()
        assert passed.wait(timeout=5.0)
        worker.stop()
