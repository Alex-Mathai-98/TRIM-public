#!/usr/bin/env python3
# In-framing: promotes the agent's own test_method_issue.py / test_fix_complete.py
# checks (a bytes method b'GET' must become the native str 'GET', not "b'GET'").
# The fix has TWO hunks: models.py PreparedRequest.prepare_method and
# sessions.py Session.request both decode bytes instead of builtin_str(). The
# minimizer kept models.py but dropped sessions.py (which the gold F2P needs), so
# we exercise BOTH paths -- the sessions.py path via a capturing transport adapter
# so no network is required.
import sys

sys.path.insert(0, "/testbed")  # import the patched /testbed copy, not site-packages

import requests
from requests.adapters import HTTPAdapter
from requests.models import PreparedRequest, Response

# (1) models.py hunk: prepare_method must decode bytes -> native str.
pr = PreparedRequest()
pr.prepare_method(b'GET')
assert pr.method == 'GET', "prepare_method(b'GET') -> %r (expected 'GET')" % (pr.method,)
assert isinstance(pr.method, str), "prepare_method result is not str: %r" % (pr.method,)

# (2) sessions.py hunk: Session.request must decode a bytes method before
#     building the request. Capture the prepared method with a fake adapter so we
#     never hit the network.
captured = {}


class _CapAdapter(HTTPAdapter):
    def send(self, request, **kwargs):
        captured['method'] = request.method
        resp = Response()
        resp.status_code = 200
        resp._content = b''
        resp.request = request
        return resp


s = requests.Session()
s.mount('http://', _CapAdapter())
try:
    s.request(b'GET', 'http://example.invalid/')
except Exception:
    pass  # post-send processing may complain about the stub Response; method is already captured
assert captured.get('method') == 'GET', \
    "Session.request(b'GET') prepared method -> %r (expected 'GET')" % (captured.get('method'),)

print("ASSERT-OK")
