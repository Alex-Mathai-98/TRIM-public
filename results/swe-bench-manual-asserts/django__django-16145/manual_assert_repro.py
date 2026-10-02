#!/usr/bin/env python3
# In-framing: promotes the agent's own test_fix.py get_addr_for_display asserts
# AND its final_test.py "Starting development server at http://0.0.0.0:8000/"
# output check. The fix has TWO hunks: (a) the new get_addr_for_display method
# and (b) the inner_run call-site that uses it. The agent's existing assert only
# covered the method, so the call-site hunk could be dropped. We protect both:
# the rendered startup message (real inner_run call site) AND the method values.
import io

import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        DEBUG=True,
        SECRET_KEY='x',
        ALLOWED_HOSTS=['*'],
        DATABASES={'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}},
        INSTALLED_APPS=['django.contrib.contenttypes', 'django.contrib.auth'],
        USE_TZ=True,
    )
django.setup()

import django.core.management.commands.runserver as rs_mod
from django.core.management.commands.runserver import Command


# --- (b) call-site hunk: the REAL inner_run must render "0.0.0.0:8000" for "0:8000".
class _StopServer(Exception):
    pass


def _fake_run(*a, **k):
    # inner_run writes the startup message BEFORE calling run(); stop here so we
    # never bind a socket / block.
    raise _StopServer()


rs_mod.run = _fake_run

out = io.StringIO()
cmd = Command(stdout=out)
cmd.addr = '0'
cmd._raw_ipv6 = False
cmd.port = '8000'
cmd.protocol = 'http'
cmd.use_ipv6 = False
cmd.check = lambda *a, **k: None            # avoid system checks / DB
cmd.check_migrations = lambda *a, **k: None
try:
    cmd.inner_run(use_threading=False, use_reloader=False, skip_checks=True,
                  shutdown_message='')
except Exception:
    pass
text = out.getvalue()
assert '0.0.0.0:8000' in text, "startup message did not display 0.0.0.0:8000:\n%s" % text
assert 'http://0:8000' not in text, "startup message still shows raw 0:8000:\n%s" % text

# --- (a) method hunk: get_addr_for_display returns the expected value per case.
for addr, raw6, expected in [
    ('0', False, '0.0.0.0'),
    ('0.0.0.0', False, '0.0.0.0'),
    ('127.0.0.1', False, '127.0.0.1'),
    ('localhost', False, 'localhost'),
    ('::1', True, '[::1]'),
    ('0', True, '[0]'),
]:
    c = Command()
    c.addr = addr
    c._raw_ipv6 = raw6
    got = c.get_addr_for_display()
    assert got == expected, "get_addr_for_display(addr=%r,ipv6=%r)=%r != %r" % (
        addr, raw6, got, expected)

print("ASSERT-OK")
