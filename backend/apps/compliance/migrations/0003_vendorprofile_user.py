"""Give a vendor profile an owner.

Until now a profile was addressed by a sequential id in the URL and belonged to
nobody, so any reader who could count could read any vendor's declared
finances — logged as docs/OPEN-QUESTIONS.md Q8 and left open deliberately rather than
half-fixed. Accounts close it: the API stops taking a profile id at all and
reads the session instead.

Nullable, and existing rows keep their null. They were created without an owner
and there is no honest way to assign one after the fact; leaving them ownerless
makes them unreachable through the API, which is what an unclaimable record
should be. Nothing is deleted — a vendor who wants their data removed is a
request to answer, not a migration to guess at.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("compliance", "0002_drop_l0_layer"),
    ]

    operations = [
        migrations.AddField(
            model_name="vendorprofile",
            name="user",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="vendor_profile",
                to="auth.user",
            ),
        ),
    ]
