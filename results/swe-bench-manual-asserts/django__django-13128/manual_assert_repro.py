import os, django
from django.conf import settings
settings.configure(DEBUG=True,
    DATABASES={'default':{'ENGINE':'django.db.backends.sqlite3','NAME':':memory:'}},
    INSTALLED_APPS=['django.contrib.contenttypes','django.contrib.auth'], USE_TZ=True)
django.setup()
from django.db import models, connection
from django.db.models import F
class Experiment(models.Model):
    start = models.DateTimeField(); end = models.DateTimeField()
    class Meta: app_label='test'
with connection.schema_editor() as se: se.create_model(Experiment)
qs = Experiment.objects.annotate(delta=F('end') - F('start'))
expr = qs.query.annotations['delta']
of = expr.output_field            # raises FieldError without the fix
itype = of.get_internal_type()
assert itype == 'DurationField', f"expected DurationField, got {itype}"
print("ASSERT-OK DurationField")
