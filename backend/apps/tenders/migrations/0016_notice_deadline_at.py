"""Store the deadline as one instant, not as a date plus a zoneless clock.

``deadline_date`` is midnight UTC of the closing day, so every query that asked
"is this still open" answered *no* from the start of the day bidding actually
ends. Measured 2026-08-07 at 11:00 UTC over the focus group: the open list held
24 notices while 27 were genuinely open — three Uzbek tenders closing that
afternoon had already been dropped, and the detail page was meanwhile counting
down to the correct instant, so the two disagreed in public.

The backfill runs in Python rather than in SQL because the conversion needs
``deadlines.resolve_deadline_instant``: a per-country zone table plus a parse of
free-text local clocks like "17:00" or "5 PM". Roughly 12,000 rows carry a
deadline, which is one bounded pass.

Reversible: the column is dropped and nothing else changes, because both source
fields are kept exactly as upstream sent them.
"""

from django.db import migrations, models


def fill_deadline_at(apps, schema_editor):
    # The real module, not a historical one — migrations get frozen models, and
    # `deadlines` is a pure function over dates and strings with no model
    # dependency at all, so importing it here cannot drift with the schema.
    from apps.tenders.deadlines import resolve_deadline_instant

    TenderNotice = apps.get_model("tenders", "TenderNotice")

    batch = []
    queryset = TenderNotice.objects.exclude(deadline_date__isnull=True).only(
        "notice_id", "deadline_date", "deadline_time", "country", "deadline_at"
    )
    for notice in queryset.iterator(chunk_size=2000):
        notice.deadline_at = resolve_deadline_instant(
            notice.deadline_date, notice.deadline_time, notice.country
        )
        batch.append(notice)
        if len(batch) >= 2000:
            TenderNotice.objects.bulk_update(batch, ["deadline_at"])
            batch.clear()
    if batch:
        TenderNotice.objects.bulk_update(batch, ["deadline_at"])


def drop_deadline_at(apps, schema_editor):
    """Nothing to undo: the column itself is removed by the schema operation."""


class Migration(migrations.Migration):
    dependencies = [
        ("tenders", "0015_harvesteddocument_origin"),
    ]

    operations = [
        migrations.AddField(
            model_name="tendernotice",
            name="deadline_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.RunPython(fill_deadline_at, drop_deadline_at),
    ]
