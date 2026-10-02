#!/usr/bin/env python3
# In-framing: promotes the agent's own test_m2m_inline_permissions.py checks
# (it printed inline.has_*_permission for users with different perms). The fix
# changes has_add/has_change/has_delete_permission of an auto-created M2M inline
# to require the CHANGE permission on the *target* model (not merely view). The
# agent's proxy didn't assert this, so the minimizer dropped the hunks. We assert
# that a VIEW-only user is denied add/change/delete on the auto-created through
# inline (base returns has_view_permission == True here -> wrong), while a user
# with CHANGE permission is granted them.
import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        DEBUG=True,
        DATABASES={'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}},
        INSTALLED_APPS=[
            'django.contrib.auth', 'django.contrib.contenttypes',
            'django.contrib.admin', 'django.contrib.sessions',
            'django.contrib.messages', '__main__',
        ],
        SECRET_KEY='x',
        USE_TZ=True,
    )
django.setup()

from django.contrib import admin
from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import Permission, User
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.db import connection, models
from django.test import RequestFactory


class Photo(models.Model):
    name = models.CharField(max_length=50)

    class Meta:
        app_label = '__main__'


class Gallery(models.Model):
    photos = models.ManyToManyField(Photo)

    class Meta:
        app_label = '__main__'


# Tables: auth/contenttypes/sessions via migrate; the __main__ models by hand.
call_command('migrate', verbosity=0, run_syncdb=True)
with connection.schema_editor() as se:
    se.create_model(Photo)
    se.create_model(Gallery)  # also auto-creates the m2m through table

site = AdminSite()


class PhotoInline(admin.TabularInline):
    model = Gallery.photos.through


inline = PhotoInline(Gallery, site)
assert inline.opts.auto_created, "through model is not auto_created"

photo_ct = ContentType.objects.get_for_model(Photo)
view_photo, _ = Permission.objects.get_or_create(
    content_type=photo_ct, codename='view_photo', defaults={'name': 'Can view photo'})
change_photo, _ = Permission.objects.get_or_create(
    content_type=photo_ct, codename='change_photo', defaults={'name': 'Can change photo'})

view_user = User.objects.create_user('viewer')
view_user.user_permissions.add(view_photo)
change_user = User.objects.create_user('changer')
change_user.user_permissions.add(change_photo)

rf = RequestFactory()


def perms(username):
    req = rf.get('/')
    req.user = User.objects.get(username=username)  # refetch -> clear perm cache
    return (inline.has_add_permission(req, None),
            inline.has_change_permission(req, None),
            inline.has_delete_permission(req, None))


# View-only user: fix denies all three (base wrongly grants via has_view_permission).
assert perms('viewer') == (False, False, False), \
    "view-only user wrongly granted inline perms: %r" % (perms('viewer'),)
# Change user: granted all three.
assert perms('changer') == (True, True, True), \
    "change user not granted inline perms: %r" % (perms('changer'),)

print("ASSERT-OK")
