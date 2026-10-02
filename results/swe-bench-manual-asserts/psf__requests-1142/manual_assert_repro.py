#!/usr/bin/env python
"""Manual assert derived from the agent's own final_test.py. The agent prepared
a GET request with no body and printed whether a Content-Length header was
present, expecting it to be absent after the fix. With the bug,
prepare_content_length unconditionally sets Content-Length: '0' for GET, so the
header is present. Promote that printed check to a hard assert.

NOTE: requests is installed non-editably into site-packages, so the patched
/testbed copy only wins if /testbed is first on sys.path. The repro runs from
/diag, so we must prepend /testbed ourselves before importing requests."""

import sys
sys.path.insert(0, "/testbed")

import requests
assert requests.__file__.startswith("/testbed/"), (
    f"imported requests from {requests.__file__}, expected /testbed copy"
)

# GET request without data: should NOT carry a Content-Length header.
req = requests.Request("GET", "http://example.com")
prepared = req.prepare()
print(f"GET no-body Content-Length present: {'Content-Length' in prepared.headers}")
assert "Content-Length" not in prepared.headers, (
    f"GET without body should have no Content-Length; "
    f"got {prepared.headers.get('Content-Length')!r}"
)

print("ASSERT-OK")
