"""Compare the Python and the Rust servers on the same requests.

Run the Python server on one port and the Rust one on another, with the same
HOME, then: python tools/compare_servers.py "/api/manga/row?name=trending" …
Fields that change with time (« updated », « last_watched ») are ignored. Ports:
PY_PORT (8081) and RS_PORT (8099).

Requests that write: --steps file.json, a list of [method, path, body]. Give each
server its own HOME then, since both would write the same database.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

PY, RS = int(os.environ.get("PY_PORT", 8081)), int(os.environ.get("RS_PORT", 8099))
IGNORED = ("updated", "last_watched")


def get(port, path, method="GET", body=None):
    started = time.time()
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body, status = response.read(), response.status
    except urllib.error.HTTPError as error:
        body, status = error.read(), error.code
    try:
        value = json.loads(body) if body[:1] in b"{[" else body
    except ValueError:
        value = body
    return status, value, time.time() - started


def norm(value):
    if isinstance(value, dict):
        return {k: norm(v) for k, v in value.items() if k not in IGNORED}
    if isinstance(value, list):
        return [norm(v) for v in value]
    return value


def main(args):
    if args[:1] == ["--steps"]:
        steps = json.load(open(args[1], encoding="utf-8"))
    else:
        steps = [["GET", path, None] for path in args]
    differ = 0
    for method, path, body in steps:
        a, b = get(PY, path, method, body), get(RS, path, method, body)
        same = a[0] == b[0] and norm(a[1]) == norm(b[1])
        differ += not same
        print(f"{'SAME' if same else 'DIFF'} {a[0]}/{b[0]} python={a[2]:.2f}s rust={b[2]:.2f}s {method} {path}")
        if not same and isinstance(a[1], dict) and isinstance(b[1], dict):
            for key in sorted(set(a[1]) | set(b[1])):
                if norm(a[1].get(key)) != norm(b[1].get(key)):
                    print("   ", key, "| python:", json.dumps(a[1].get(key), ensure_ascii=False)[:300],
                          "| rust:", json.dumps(b[1].get(key), ensure_ascii=False)[:300])
    return 1 if differ else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
