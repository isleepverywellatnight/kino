"""One SSL context for all of KINO: building one per request while other threads
read HTTPS answers crashed KINO on Windows."""
import ssl
import threading
import unittest
import urllib.request

import config
import kino_update


class SharedContextTests(unittest.TestCase):
    def test_one_context_for_every_thread(self):
        seen = []
        threads = [threading.Thread(target=lambda: seen.append(config.get_ssl_context())) for _ in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len({id(c) for c in seen}), 1)
        self.assertIsInstance(seen[0], ssl.SSLContext)
        self.assertEqual(seen[0].verify_mode, ssl.CERT_REQUIRED, "certificates are still checked")
        self.assertIs(kino_update._https_context(), kino_update._https_context())

    def test_urlopen_without_a_context_uses_the_shared_one(self):
        handlers = [h for h in urllib.request._opener.handlers if isinstance(h, urllib.request.HTTPSHandler)]
        self.assertEqual([h._context for h in handlers], [config.get_ssl_context()])


if __name__ == "__main__":
    unittest.main()
