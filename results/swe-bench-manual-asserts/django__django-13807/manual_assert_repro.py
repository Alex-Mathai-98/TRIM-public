#!/usr/bin/env python3
# In-framing: promotes the agent's own simple_test.py check — calling
# connection.check_constraints on a model whose db_table is a SQL reserved word
# ('order') must NOT raise (the fix quote_name()s the table/column names inside
# the sqlite PRAGMA foreign_key_check / foreign_key_list / detail SELECTs). At
# base the unquoted keyword produces an OperationalError (syntax error). We also
# exercise an actual FK violation between keyword-named tables so the
# violation-detail quoting path runs, distinguishing "quoting works" (IntegrityError
# raised for the bad row) from "quoting broken" (OperationalError syntax error).
import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        DEBUG=True,
        DATABASES={'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}},
        INSTALLED_APPS=['django.contrib.contenttypes', 'django.contrib.auth', '__main__'],
        USE_TZ=True,
    )
django.setup()

from django.db import connection, models
from django.db.utils import IntegrityError, OperationalError


class Reporter(models.Model):           # keyword table name
    name = models.CharField(max_length=50)

    class Meta:
        app_label = '__main__'
        db_table = 'order'


class Article(models.Model):            # another keyword table name, FK to Reporter
    reporter = models.ForeignKey(Reporter, models.CASCADE)

    class Meta:
        app_label = '__main__'
        db_table = 'select'


with connection.schema_editor() as se:
    se.create_model(Reporter)
    se.create_model(Article)

# (1) No-violation: check_constraints on keyword tables must succeed (base raises
#     OperationalError on the unquoted `PRAGMA foreign_key_check(order)`).
Reporter.objects.create(name='r')
connection.check_constraints(['order', 'select'])

# (2) Violation path: insert a dangling FK with constraints off, then verify
#     check_constraints detects it (IntegrityError) rather than dying on a
#     syntax error (OperationalError) from the unquoted keyword names.
with connection.cursor() as c:
    c.execute('PRAGMA foreign_keys = OFF')
    c.execute('INSERT INTO "select" (id, reporter_id) VALUES (1, 999)')
    c.execute('PRAGMA foreign_keys = ON')

try:
    connection.check_constraints(['select', 'order'])
    # No exception is acceptable too (depends on the sqlite check path), as long
    # as it wasn't an OperationalError syntax failure.
except IntegrityError:
    pass  # violation correctly detected — names were quoted properly
except OperationalError as e:
    raise AssertionError("unquoted keyword caused a syntax error (bug present): %s" % e)

print("ASSERT-OK")
