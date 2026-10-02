#!/usr/bin/env python3
# In-framing: promotes the agent's own reproduce_issue.py deconstruct-path
# comparisons (it printed/asserted simplified paths for F, Value, Case, When)
# to the FULL set of classes the fix decorates. The fix adds
# @deconstructible(path='django.db.models.<Name>') to 14 expression classes;
# the agent only covered 3, so the minimizer dropped the other 11 decorator
# hunks. We assert the simplified deconstruct path for EVERY decorated class so
# none of those hunks can be dropped.
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

from django.contrib.auth.models import User
from django.db.models import (
    Case, Exists, ExpressionWrapper, F, Func, IntegerField, OrderBy, OuterRef,
    Q, Subquery, Sum, Value, When, Window,
)
from django.db.models.expressions import (
    ExpressionList, RowRange, ValueRange, WindowFrame,
)

# (Name, constructed instance) for each class the fix decorates with
# @deconstructible(path='django.db.models.<Name>').
cases = [
    ("OuterRef", OuterRef('x')),
    ("Func", Func(Value(1))),
    ("Value", Value(1)),
    ("ExpressionList", ExpressionList(F('a'))),
    ("ExpressionWrapper", ExpressionWrapper(Value(1), output_field=IntegerField())),
    ("When", When(condition=Q(pk=1), then=Value(1))),
    ("Case", Case(When(condition=Q(pk=1), then=Value(1)))),
    ("Subquery", Subquery(User.objects.all())),
    ("Exists", Exists(User.objects.all())),
    ("OrderBy", OrderBy(F('a'))),
    ("Window", Window(expression=Sum('pk'))),
    ("WindowFrame", WindowFrame()),
    ("RowRange", RowRange()),
    ("ValueRange", ValueRange()),
]

for name, obj in cases:
    path = obj.deconstruct()[0]
    expected = 'django.db.models.%s' % name
    assert path == expected, "%s: deconstruct path %r != %r" % (name, path, expected)

print("ASSERT-OK", len(cases), "classes")
