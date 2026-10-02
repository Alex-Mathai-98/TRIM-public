#!/usr/bin/env python3
# In-framing: promotes the agent's own test_inheritance.py signature check
# (execute_sql_flush params must be ['self','sql_list']) and test_comprehensive.py
# "new signature works / old signature fails" checks to hard asserts. The fix has
# TWO coupled hunks: operations.py drops the `using` param (uses
# self.connection.alias), and flush.py updates the call site to one arg. The
# agent's proxy only printed the signature, so the minimizer dropped the hunks.
# We assert (1) the new 1-arg signature [protects operations.py] and (2) the real
# `flush` management command runs end-to-end [protects the flush.py call-site
# coupling: an ops=1-arg + flush.py=2-arg inconsistent state raises].
import inspect

import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        DEBUG=True,
        DATABASES={'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}},
        INSTALLED_APPS=['django.contrib.contenttypes', 'django.contrib.auth'],
        USE_TZ=True,
    )
django.setup()

from django.core.management import call_command
from django.core.management.color import no_style
from django.db import connections
from django.db.backends.base.operations import BaseDatabaseOperations

# (1) operations.py hunk: signature must be (self, sql_list) — `using` removed.
params = list(inspect.signature(BaseDatabaseOperations.execute_sql_flush).parameters)
assert params == ['self', 'sql_list'], "execute_sql_flush params %r != ['self','sql_list']" % params

# one positional arg must work (at base it raises TypeError: missing 'sql_list').
conn = connections['default']
with conn.cursor() as c:
    c.execute("CREATE TABLE ma_t (id integer primary key, n text)")
    c.execute("INSERT INTO ma_t (n) VALUES ('a')")
sql_list = conn.ops.sql_flush(no_style(), ['ma_t'])
conn.ops.execute_sql_flush(sql_list)
with conn.cursor() as c:
    c.execute("SELECT COUNT(*) FROM ma_t")
    assert c.fetchone()[0] == 0, "execute_sql_flush(one-arg) did not flush"

# (2) flush.py call-site coupling: the real flush command must run without error.
call_command('migrate', verbosity=0, run_syncdb=True)
call_command('flush', verbosity=0, interactive=False)

print("ASSERT-OK")
