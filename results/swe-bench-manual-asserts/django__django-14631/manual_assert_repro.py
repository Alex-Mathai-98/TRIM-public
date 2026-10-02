#!/usr/bin/env python3
"""Asserting reproduction for django__django-14631 (disabled-field initial consistency).

Based on the agent's own reproductions (debug_test.py / test_pr_scenario.py): for a
disabled DateTimeField with a callable initial, form['dt'].initial must equal
form.cleaned_data['dt'] (the agent printed this comparison as the success criterion).
On the buggy code _clean_fields() computes the value differently from BoundField.initial,
so they differ; the fix routes both through BoundField, keeping them equal.
"""
import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        DEBUG=True,
        SECRET_KEY='test-secret-key',
        USE_TZ=False,
    )

django.setup()

import datetime
from django.forms import Form, DateTimeField

now = datetime.datetime(2006, 10, 25, 14, 30, 45, 123456)


class DateTimeForm(Form):
    dt = DateTimeField(initial=lambda: now, disabled=True)


form = DateTimeForm({})
bound_initial = form['dt'].initial
form.full_clean()
cleaned_value = form.cleaned_data['dt']

# Agent's own comparison promoted to a hard assert.
assert bound_initial == cleaned_value, (
    f"BoundField.initial ({bound_initial!r}) must equal cleaned_data ({cleaned_value!r})"
)

print("ASSERT-OK")
