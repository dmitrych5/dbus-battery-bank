from collections import deque
from struct import pack

import pytest

from battery_bank.acquisition.availability import MAX_AVAILABILITY_RETRIES
from battery_bank.acquisition.battery_poller import MAX_SET_SOC_RETRIES, PackPoller
from battery_bank.acquisition.shunt_poller import ShuntPoller
from battery_bank.config import BatteryPortConfig
from battery_bank.transport import up16s
from tests.test_up16s import response_frame
from tests.test_vedirect import HISTORY_FIELDS, SHUNT_FIELDS, frame_bytes

PORT = "/dev/ttyUSB0"


def pack_status_payload(serial_number=b"SN-1", rated_capacity=10500, cell_voltages=(3301, 3302, 3303, 3304), temperatures=(700, 705)):
    prefix = [0] * 30
    prefix[6] = rated_capacity
    payload = up16s.PackStatus.PREFIX_STRUCT.pack(*prefix)
    payload += pack(f">H{len(cell_voltages)}H", len(cell_voltages), *cell_voltages)
    payload += pack(f">H{len(temperatures)}H", len(temperatures), *temperatures)
    payload += up16s.PackStatus.SUFFIX_STRUCT.pack(0, 0, 0x0C01, serial_number.ljust(30, b"\x00"))
    return payload


def params1_payload(model_and_sn=b"UP16S015-SN123"):
    return up16s.PackParams1.STRUCT.pack(b"\x00" * 16, model_and_sn.ljust(30, b"\x00"), 2024, 5, 1, b"PACK-1".ljust(30, b"\x00"), 2024, 6, 2)


def product_information_payload():
    return up16s.ProductInformation.STRUCT.pack(1, 2, 12, 1, 7, 0, b"UP16S015".ljust(16, b"\x00"), b"JBD".ljust(16, b"\x00"))


class FakeLink:
    """Scripted request->response mapping; missing entries time out (None)."""

    def __init__(self):
        self.responses: dict[bytes, deque] = {}
        self.requests: list[bytes] = []
        self.interference_queue: deque[bool] = deque()
        self.reopen_count = 0

    def respond(self, address, command, payload, request_payload=b"", repeat=1):
        request = up16s.build_request(address, command, request_payload)
        self.responses.setdefault(request, deque()).extend([response_frame(address, command, payload)] * repeat)

    def request(self, request_bytes, *_):
        self.requests.append(request_bytes)
        queue = self.responses.get(request_bytes)
        return queue.popleft() if queue else None

    def read_available(self):
        return b""

    def interference_detected(self):
        return self.interference_queue.popleft() if self.interference_queue else False

    def reopen(self):
        self.reopen_count += 1


def make_poller(link, addresses=(1, 2), require_direct_connection=False, cells_per_pack=4):
    config = BatteryPortConfig(device=PORT, pack_addresses=addresses, require_direct_connection=require_direct_connection)
    return PackPoller(config, link, cells_per_pack, clock=lambda: 1000.0, sleep=lambda _: None)


def script_full_pack(link, address, repeat=1):
    link.respond(address, up16s.PackStatus, pack_status_payload(serial_number=f"SN-{address}".encode()), repeat=repeat)
    link.respond(address, up16s.PackParams1, params1_payload(model_and_sn=f"UP16S-SN{address}".encode()))
    link.respond(address, up16s.ProductInformation, product_information_payload())


class TestDiscovery:
    def test_discovers_identity_from_pack_params1(self):
        link = FakeLink()
        script_full_pack(link, 1)
        script_full_pack(link, 2)
        infos = make_poller(link).discover()
        assert infos[1].unique_id == "UP16S-SN1"
        assert infos[2].unique_id == "UP16S-SN2"
        assert "FW v12.1.7" in infos[1].hardware_description
        assert infos[1].production_description == "BMS 2024.05.01, Pack 2024.06.02"

    def test_pack_params1_unavailable_falls_back_to_pack_serial_and_skips_product_information(self):
        link = FakeLink()
        link.respond(1, up16s.PackStatus, pack_status_payload(serial_number=b"SN-1", rated_capacity=10500))
        infos = make_poller(link, addresses=(1,)).discover()
        assert infos[1].unique_id == "SN-1_105.0"
        product_information_request = up16s.build_request(1, up16s.ProductInformation)
        assert product_information_request not in link.requests
        params1_request = up16s.build_request(1, up16s.PackParams1)
        assert link.requests.count(params1_request) == MAX_AVAILABILITY_RETRIES

    def test_unresponsive_pack_is_skipped_and_retried_on_next_discover(self):
        link = FakeLink()
        script_full_pack(link, 1)
        poller = make_poller(link)
        assert set(poller.discover()) == {1}
        script_full_pack(link, 2)
        assert set(poller.discover()) == {1, 2}

    def test_interference_during_discovery_reopens_and_retries(self):
        link = FakeLink()
        request = up16s.build_request(1, up16s.PackStatus)
        link.responses[request] = deque([None, response_frame(1, up16s.PackStatus, pack_status_payload())])
        link.interference_queue.extend([True])
        link.respond(1, up16s.PackParams1, params1_payload())
        link.respond(1, up16s.ProductInformation, product_information_payload())
        infos = make_poller(link, addresses=(1,)).discover()
        assert 1 in infos
        assert link.reopen_count == 1

    def test_require_direct_connection_needs_individual_pack_status(self):
        link = FakeLink()
        script_full_pack(link, 1)
        infos = make_poller(link, addresses=(1,), require_direct_connection=True).discover()
        assert infos == {}

    def test_pack_with_wrong_cell_count_is_not_accepted(self):
        link = FakeLink()
        script_full_pack(link, 1)
        infos = make_poller(link, addresses=(1,), cells_per_pack=16).discover()
        assert infos == {}


class TestPolling:
    def discovered_poller(self, link, addresses=(1, 2)):
        for address in addresses:
            script_full_pack(link, address)
        poller = make_poller(link, addresses=addresses)
        poller.discover()
        return poller

    def test_master_gets_chain_and_individual_limits(self):
        link = FakeLink()
        poller = self.discovered_poller(link)
        link.respond(1, up16s.PackStatus, pack_status_payload())
        link.respond(1, up16s.IndividualPackStatus, pack(">96sHH", b"\x00" * 96, 120, 2000))
        link.respond(2, up16s.PackStatus, pack_status_payload())
        snapshots = poller.poll()
        by_address = {snapshot.identity.address: snapshot for snapshot in snapshots}
        assert by_address[1].chain_aggregated_limits is not None
        assert by_address[1].bms_limits.charge_current_amps == pytest.approx(12.0)
        assert by_address[2].chain_aggregated_limits is None

    def test_snapshot_with_deviating_counts_is_dropped(self):
        """Publishing fixes per-cell and per-sensor D-Bus paths from the first snapshot; a
        frame with different counts must be treated as a failed poll."""
        link = FakeLink()
        poller = self.discovered_poller(link, addresses=(2,))
        link.respond(2, up16s.PackStatus, pack_status_payload(temperatures=(700, 705, 710)))
        assert poller.poll() == []
        link.respond(2, up16s.PackStatus, pack_status_payload(cell_voltages=(3301, 3302, 3303)))
        assert poller.poll() == []
        link.respond(2, up16s.PackStatus, pack_status_payload())
        assert len(poller.poll()) == 1

    def test_unresponsive_pack_yields_no_snapshot(self):
        link = FakeLink()
        poller = self.discovered_poller(link)
        link.respond(2, up16s.PackStatus, pack_status_payload())
        snapshots = poller.poll()
        assert [snapshot.identity.address for snapshot in snapshots] == [2]

    def test_params2_becomes_unavailable_after_repeated_timeouts(self):
        link = FakeLink()
        poller = self.discovered_poller(link, addresses=(2,))
        params2_request = up16s.build_request(2, up16s.PackParams2)
        for _ in range(MAX_AVAILABILITY_RETRIES + 2):
            link.respond(2, up16s.PackStatus, pack_status_payload())
            poller.poll()
        assert link.requests.count(params2_request) == MAX_AVAILABILITY_RETRIES

    def test_soc_reset_request_sends_set_soc_on_next_poll(self):
        link = FakeLink()
        poller = self.discovered_poller(link, addresses=(2,))
        assert poller.request_soc_reset("UP16S-SN2", 100.0) is True
        set_soc_request = up16s.build_request(2, up16s.SetSoc, up16s.SetSoc.request_payload(100.0))
        link.responses[set_soc_request] = deque([response_frame(2, up16s.SetSoc, b"")])
        link.respond(2, up16s.PackStatus, pack_status_payload())
        poller.poll()
        assert set_soc_request in link.requests

    def test_soc_reset_works_when_pack_params2_is_unavailable(self):
        """PackParams2 can be unavailable merely because old firmware ignores its partial-read
        request format; the plain SetSoc write still works there and must not be refused."""
        link = FakeLink()
        poller = self.discovered_poller(link, addresses=(2,))
        for _ in range(MAX_AVAILABILITY_RETRIES):
            link.respond(2, up16s.PackStatus, pack_status_payload())
            poller.poll()
        assert poller.request_soc_reset("UP16S-SN2", 100.0) is True
        set_soc_request = up16s.build_request(2, up16s.SetSoc, up16s.SetSoc.request_payload(100.0))
        link.responses[set_soc_request] = deque([response_frame(2, up16s.SetSoc, b"")])
        poller.poll()
        assert set_soc_request in link.requests

    def test_soc_reset_refused_after_set_soc_write_attempts_are_exhausted(self):
        link = FakeLink()
        poller = self.discovered_poller(link, addresses=(2,))
        polls_to_exhaust = -(-MAX_AVAILABILITY_RETRIES // MAX_SET_SOC_RETRIES)
        for _ in range(polls_to_exhaust):
            assert poller.request_soc_reset("UP16S-SN2", 100.0) is True
            poller.poll()
        assert poller.request_soc_reset("UP16S-SN2", 100.0) is False
        set_soc_request = up16s.build_request(2, up16s.SetSoc, up16s.SetSoc.request_payload(100.0))
        assert link.requests.count(set_soc_request) == MAX_AVAILABILITY_RETRIES

    def test_soc_reset_for_unknown_pack_or_invalid_value_is_refused(self):
        link = FakeLink()
        poller = self.discovered_poller(link, addresses=(2,))
        assert poller.request_soc_reset("nonexistent", 100.0) is False
        assert poller.request_soc_reset("UP16S-SN2", 101.0) is False


class TestShuntPoller:
    class FakeShuntLink:
        def __init__(self, chunks):
            self.chunks = deque(chunks)
            self.reopen_count = 0
            self.interference = False

        def read_available(self):
            return self.chunks.popleft() if self.chunks else b""

        def interference_detected(self):
            return self.interference

        def reopen(self):
            self.reopen_count += 1

    def test_returns_latest_valid_snapshot(self):
        link = self.FakeShuntLink([frame_bytes(SHUNT_FIELDS) * 3])
        poller = ShuntPoller(link, clock=lambda: 1000.0)
        snapshot = poller.poll()
        assert snapshot.current_amps == pytest.approx(-5.0)
        assert snapshot.aux_voltage_volts == pytest.approx(0.777)
        assert snapshot.taken_at_monotonic == 1000.0

    def test_no_new_data_returns_previous_snapshot(self):
        link = self.FakeShuntLink([frame_bytes(SHUNT_FIELDS) * 3])
        poller = ShuntPoller(link, clock=lambda: 1000.0)
        first = poller.poll()
        assert poller.poll() is first

    def test_energy_totals_carry_from_the_history_frame_into_snapshots(self):
        alternating = frame_bytes(SHUNT_FIELDS) + frame_bytes(HISTORY_FIELDS) + frame_bytes(SHUNT_FIELDS) * 2
        link = self.FakeShuntLink([alternating])
        snapshot = ShuntPoller(link, clock=lambda: 1000.0).poll()
        assert snapshot.history_totals.charged_energy_kwh == pytest.approx(1500.0)
        assert snapshot.history_totals.discharged_energy_kwh == pytest.approx(1400.0)
        assert snapshot.history_totals.automatic_sync_count == 250

    def test_totals_unknown_until_the_history_frame_arrives(self):
        link = self.FakeShuntLink([frame_bytes(SHUNT_FIELDS) * 3])
        snapshot = ShuntPoller(link, clock=lambda: 1000.0).poll()
        assert snapshot.history_totals is None

    def test_interference_triggers_reopen(self):
        link = self.FakeShuntLink([])
        link.interference = True
        ShuntPoller(link).poll()
        assert link.reopen_count == 1
