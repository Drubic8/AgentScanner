from copy import deepcopy
import json
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "antminer_stock.json"


def stock():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class FakeTransport:
    """Counting transport; all inputs are synthetic, no real network access."""
    def __init__(self, factory, ip, operation, credentials=None):
        self.factory, self.ip, self.operation = factory, ip, operation

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def value(self, key):
        self.operation.remaining()
        self.operation.request_count += 1
        self.factory.calls.append((self.ip, key))
        value = self.factory.data.get(key)
        if isinstance(value, Exception):
            raise value
        return deepcopy(value)

    def cgminer(self, command, **kwargs):
        return self.value(command)

    def rpc(self, command, parameter=None):
        return self.value({"get.device.info": "rpc_info", "get.miner.status": "rpc_" + str(parameter)}.get(command, command))

    def http_json(self, path, method="GET", **kwargs):
        key = {
            "/cgi-bin/get_system_info.cgi": "system", "/cgi-bin/get_miner_conf.cgi": "config",
            "/api/v1/info": "vnish_info", "/api/v1/summary": "vnish_summary",
            "/cgi-bin/luci/stats.cgi": "elphapex_stats",
            "/cgi-bin/luci/get_miner_conf.cgi": "elphapex_config",
            "/cgi-bin/minerStatus.cgi": "jasminer_status",
        }.get(path, path)
        return self.value(key)

    def http(self, path, method="GET", *, payload=None, **kwargs):
        self.operation.remaining()
        self.operation.request_count += 1
        self.factory.calls.append((self.ip, path))
        if method == "POST" or "reboot" in path:
            self.factory.writes.append((path, method, deepcopy(payload)))
            if self.factory.write_error:
                raise self.factory.write_error
            if self.factory.apply_mode and path.endswith("set_miner_conf.cgi"):
                self.factory.data["config"].update(payload)
            return 200, b'{"code":"B000"}'
        return 404, b""


class FakeFactory:
    def __init__(self, data=None):
        self.data = stock() if data is None else data
        self.calls, self.writes = [], []
        self.apply_mode = True
        self.write_error = None

    def __call__(self, ip, operation, credentials=None):
        return FakeTransport(self, ip, operation, credentials)
