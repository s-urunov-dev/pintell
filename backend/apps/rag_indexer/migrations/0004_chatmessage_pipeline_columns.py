"""Which model wrote an answer, and whether one was called at all.

Three columns on ``ChatMessage`` so the pipeline in front of the model (D57,
D60) can be measured rather than asserted: the cache's hit rate per tier, the
router's split, and whether the fast tier's ``unsupported`` count differs from
the deep tier's. All blank on existing rows, which is the honest value — those
answers were written before there was a decision to record.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("rag_indexer", "0003_similaraward"),
    ]

    operations = [
        migrations.AddField(
            model_name="chatmessage",
            name="model",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="chatmessage",
            name="route_tier",
            field=models.CharField(blank=True, max_length=16),
        ),
        migrations.AddField(
            model_name="chatmessage",
            name="cache_tier",
            field=models.CharField(blank=True, max_length=16),
        ),
    ]
