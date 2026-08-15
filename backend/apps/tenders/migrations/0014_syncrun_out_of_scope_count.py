from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tenders", "0013_harvesteddocument"),
    ]

    operations = [
        migrations.AddField(
            model_name="syncrun",
            name="out_of_scope_count",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
