#!/usr/bin/env python3
import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        USE_I18N=True,
        USE_L10N=True,
        USE_TZ=True,
        SECRET_KEY='test-key-for-validation-testing',
    )
    django.setup()

from django.contrib.auth.validators import ASCIIUsernameValidator, UnicodeUsernameValidator
from django.core.exceptions import ValidationError


def is_valid(validator, value):
    try:
        validator(value)
        return True
    except ValidationError:
        return False


ascii_validator = ASCIIUsernameValidator()
unicode_validator = UnicodeUsernameValidator()

# Agent's own reproduction: a username with a trailing newline must be rejected.
# Buggy regex r'^[\w.@+-]+$' incorrectly accepts 'testuser\n' (matches valid==True);
# expected result is invalid (validation error raised).
assert is_valid(ascii_validator, 'testuser\n') == False, \
    "ASCII validator must reject 'testuser\\n' (trailing newline)"
assert is_valid(unicode_validator, 'testuser\n') == False, \
    "Unicode validator must reject 'testuser\\n' (trailing newline)"

# Normal usernames must still be accepted.
assert is_valid(ascii_validator, 'testuser') == True
assert is_valid(unicode_validator, 'testuser') == True

print("ASSERT-OK")
