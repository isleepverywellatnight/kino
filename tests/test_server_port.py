"""KINO keeps its usual port after a restart, but never shares it with a live server."""

import socket
import unittest

import config


def _listener(address="0.0.0.0"):
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((address, 0))
    server.listen()
    return server


class ServerPortTests(unittest.TestCase):
    def test_connections_left_by_the_previous_instance_do_not_move_the_port(self):
        server = _listener()
        port = server.getsockname()[1]
        client = socket.create_connection(("127.0.0.1", port))
        accepted, _ = server.accept()
        accepted.close()  # the server side closes first and keeps the port in TIME_WAIT
        client.close()
        server.close()
        self.assertEqual(config.find_available_port(port, max_tries=3), port)

    def test_a_live_loopback_server_keeps_its_port(self):
        for address in ("0.0.0.0", "127.0.0.1"):
            with self.subTest(address=address):
                server = _listener(address)
                try:
                    port = server.getsockname()[1]
                    self.assertFalse(config.port_is_free(port))
                    self.assertNotEqual(config.find_available_port(port, max_tries=3), port)
                finally:
                    server.close()


if __name__ == "__main__":
    unittest.main()


class ParallelRequestTests(unittest.TestCase):
    def test_page_load_bursts_are_not_reset(self):
        import threading
        import time
        import urllib.request
        from http.server import BaseHTTPRequestHandler

        import app

        class SlowHandler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                time.sleep(0.05)
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")

        server = app.ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]
        failures = []

        def fetch():
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10) as response:
                    response.read()
            except Exception as exc:  # noqa: BLE001 - every failure counts here
                failures.append(exc)

        threads = [threading.Thread(target=fetch) for _ in range(60)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(failures, [])
