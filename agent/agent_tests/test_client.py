"""HTTP-клиент агента: сверка часов с сервером."""
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tbo_agent.client import Api

SHIFT = 100.0          # часы сервера впереди на 100 с


class _H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _reply(self):
        if self.path.startswith("/api/agent/commands"):
            time.sleep(1.5)                                  # долгий опрос: время ставится в конце ожидания
        now = datetime.now(timezone.utc) + timedelta(seconds=SHIFT)
        body = json.dumps({"command": None, "server_time": now.isoformat().replace("+00:00", "Z")}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = _reply


def test_long_poll_does_not_skew_clock():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        api = Api("http://127.0.0.1:%d" % srv.server_address[1], "tbo_test", timeout=5)
        api.state({"state": "idle"})
        assert abs(api.clock_offset - SHIFT) < 0.5
        api.next_command(wait=1)                             # прежде: смещение «уезжало» на ~0,75 с (половина ожидания)
        assert abs(api.clock_offset - SHIFT) < 0.5
    finally:
        srv.shutdown()


def test_outbox_keeps_newest_logs(tmp_path):
    import os

    from tbo_agent.agent import Outbox
    ob = Outbox(str(tmp_path / "outbox"))
    for rid in range(1, 36):                                 # 35 журналов: run-1 … run-35
        ob.put(rid, {"n": rid})
        ob.done("run-%d.json" % rid, "rejected")
        os.utime(tmp_path / "rejected" / ("run-%d.json" % rid), (rid * 100, rid * 100))
    left = sorted(int(f[4:-5]) for f in os.listdir(tmp_path / "rejected"))
    assert left == list(range(6, 36))                        # остались 30 последних, а не «по алфавиту»
