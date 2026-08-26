"""
Give every user a UUID identity for the DDL-owned schemas.

Three steps, and the first one deliberately has no default. `AddField`
evaluates a Python default *once* and hands the single resulting value to one
ALTER, so adding a `default=uuid.uuid4` column to a populated table gives every
existing row the same UUID — and then the unique index cannot be built. Adding
it nullable leaves the existing rows genuinely empty, which is what lets the
backfill give each one its own value.
"""

import uuid

from django.db import migrations, models


def backfill_public_ids(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    for pk in User.objects.filter(public_id__isnull=True).values_list("pk", flat=True):
        User.objects.filter(pk=pk).update(public_id=uuid.uuid4())


class Migration(migrations.Migration):
    dependencies = [("accounts", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="user",
            name="public_id",
            field=models.UUIDField(editable=False, null=True),
        ),
        migrations.RunPython(backfill_public_ids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="user",
            name="public_id",
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
    ]
