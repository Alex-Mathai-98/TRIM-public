#!/usr/bin/env python
import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        DEBUG=True,
        DATABASES={
            'default': {
                'ENGINE': 'django.db.backends.sqlite3',
                'NAME': ':memory:',
            }
        },
        INSTALLED_APPS=[
            'django.contrib.auth',
            'django.contrib.contenttypes',
        ],
        SECRET_KEY='test-secret-key',
    )

django.setup()

from django.contrib.auth.models import User
from django.db.models.fields import IntegerField
from django.utils.functional import SimpleLazyObject

# Agent's own reproduction (test_all_integer_fields.py): a SimpleLazyObject wrapping
# a model instance whose pk=123 must be coerced by IntegerField.get_prep_value to 123.
# Buggy code does int(user) which raises TypeError; expected value is the pk = 123.
user = User(username="testuser")
user.pk = 123

lazy_user = SimpleLazyObject(lambda: user)

field = IntegerField()
result = field.get_prep_value(lazy_user)
assert result == 123, f"expected pk 123, got {result!r}"

print("ASSERT-OK")
