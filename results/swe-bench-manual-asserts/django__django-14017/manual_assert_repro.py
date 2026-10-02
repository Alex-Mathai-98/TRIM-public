#!/usr/bin/env python
"""Asserting reproduction for django__django-14017 (Q() & Exists(...) commutativity).

Based on the agent's own reproductions (reproduce_issue.py / test_original_issue.py):
the original issue is that `Q() & Exists(...)` raises TypeError while `Exists(...) & Q()`
works. The fix makes both directions succeed. We assert both combinations succeed and
return a combinable Q object.
"""
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
            'django.contrib.contenttypes',
            'django.contrib.auth',
        ],
        USE_TZ=True,
    )

django.setup()

from django.db import models
from django.db.models import Q, Exists


class Product(models.Model):
    name = models.CharField(max_length=100)

    class Meta:
        app_label = 'test'


# Direction that already worked even on the buggy code.
res_a = Exists(Product.objects.all()) & Q()
assert isinstance(res_a, Q), f"Exists(...) & Q() must return a Q, got {type(res_a)}"

# Direction that raised TypeError on the buggy code (the actual bug).
res_b = Q() & Exists(Product.objects.all())
assert isinstance(res_b, Q), f"Q() & Exists(...) must return a Q, got {type(res_b)}"

# Same for | (also part of the agent's reproduction).
res_c = Q() | Exists(Product.objects.all())
assert isinstance(res_c, Q), f"Q() | Exists(...) must return a Q, got {type(res_c)}"

print("ASSERT-OK")
