"""Record how each mirrored document reached us (DECISIONS.md D17).

Every existing row was found by following a link, so the default is correct for
the whole table and no data migration is needed. The column is indexed because
both consumers filter on it: the harvest queue excludes client-supplied rows
(there is nothing to fetch), and L3's second phase selects only them.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tenders", "0014_syncrun_out_of_scope_count"),
    ]

    operations = [
        migrations.AddField(
            model_name="harvesteddocument",
            name="origin",
            field=models.CharField(
                choices=[
                    ("harvested", "Found via a link we mirrored"),
                    ("client_supplied", "Supplied by a vendor"),
                ],
                db_index=True,
                default="harvested",
                max_length=16,
            ),
        ),
    ]
