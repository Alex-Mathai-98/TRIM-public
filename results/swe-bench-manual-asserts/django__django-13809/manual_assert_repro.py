#!/usr/bin/env python
"""Asserting reproduction for django__django-13809 (--skip-checks on runserver).

Based on the agent's own reproductions (test_cli_skip_checks.py / final_verification.py):
the runserver command must expose a --skip-checks option. Buggy (base) runserver has
no such option; the fix adds it. We assert the option exists on the command parser
and that runserver respects skip_checks=True by NOT performing system checks.
"""
import django
from django.conf import settings
from io import StringIO

if not settings.configured:
    settings.configure(
        DEBUG=True,
        SECRET_KEY='test-secret-key',
        INSTALLED_APPS=[
            'django.contrib.auth',
            'django.contrib.contenttypes',
        ],
        DATABASES={
            'default': {
                'ENGINE': 'django.db.backends.sqlite3',
                'NAME': ':memory:',
            }
        },
        ALLOWED_HOSTS=['*'],
    )

django.setup()

from django.core.management.commands.runserver import Command as RunserverCommand

# --- Assertion 1: the option must be in the parser help (agent's CLI repro). ---
command = RunserverCommand()
parser = command.create_parser('manage.py', 'runserver')
help_text = parser.format_help()
assert '--skip-checks' in help_text, "runserver must expose a --skip-checks option"

# --- Assertion 2: when skip_checks=True, inner_run must NOT perform system checks. ---
# Agent's repro mocked check/check_migrations and asserted they were skipped.
check_called = {'check': False, 'migrations': False}

cmd = RunserverCommand(stdout=StringIO(), stderr=StringIO())
cmd.check = lambda *a, **k: check_called.__setitem__('check', True)
cmd.check_migrations = lambda *a, **k: check_called.__setitem__('migrations', True)
cmd.get_handler = lambda *a, **k: None
# Avoid actually binding a socket / running the server loop.
def _no_run(addr, port, wsgi_handler, **kwargs):
    raise SystemExit
import django.core.servers.basehttp as basehttp
basehttp.run = _no_run

try:
    cmd.inner_run(None, addrport='127.0.0.1:0', use_ipv6=False,
                  use_threading=True, use_reloader=False, skip_checks=True)
except SystemExit:
    pass
except Exception:
    pass

assert check_called['check'] is False, "system check must be SKIPPED when skip_checks=True"
assert check_called['migrations'] is False, "migration check must be SKIPPED when skip_checks=True"

print("ASSERT-OK")
