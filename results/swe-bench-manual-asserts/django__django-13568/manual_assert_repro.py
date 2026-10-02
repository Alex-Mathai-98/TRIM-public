#!/usr/bin/env python
"""Asserting reproduction for auth.E003 + UniqueConstraint (agent's own scenario)."""
import os
import sys
import tempfile
import django
from django.conf import settings

temp_dir = tempfile.mkdtemp()
sys.path.insert(0, temp_dir)

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
            'testapp',
        ],
        SECRET_KEY='test-secret-key',
        USE_TZ=True,
        AUTH_USER_MODEL='testapp.User',
    )

app_dir = os.path.join(temp_dir, 'testapp')
os.makedirs(app_dir)
with open(os.path.join(app_dir, '__init__.py'), 'w') as f:
    f.write('')
with open(os.path.join(app_dir, 'apps.py'), 'w') as f:
    f.write('''
from django.apps import AppConfig

class TestappConfig(AppConfig):
    name = 'testapp'
    default_auto_field = 'django.db.models.BigAutoField'
''')
with open(os.path.join(app_dir, 'models.py'), 'w') as f:
    f.write('''
from django.contrib.auth.base_user import AbstractBaseUser
from django.db import models
from django.db.models import UniqueConstraint

class User(AbstractBaseUser):
    username = models.CharField(max_length=30)
    USERNAME_FIELD = "username"

    class Meta:
        constraints = [UniqueConstraint(fields=["username"], name="user_username_unq")]
''')

django.setup()

from django.contrib.auth.checks import check_user_model

# Agent's own reproduction (final_test/test_system_check): a USERNAME_FIELD made unique
# via a UniqueConstraint must NOT raise auth.E003. Buggy check only looks at field.unique
# and emits auth.E003; expected: zero E003 errors.
errors = check_user_model()
e003 = [e for e in errors if getattr(e, 'id', None) == 'auth.E003']
assert e003 == [], f"unexpected auth.E003 errors: {e003}"

print("ASSERT-OK")
