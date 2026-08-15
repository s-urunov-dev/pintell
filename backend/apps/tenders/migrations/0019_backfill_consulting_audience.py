"""Fill `consulting_audience` on notices that were already classified.

`classify_pending` only ever looks at notices whose category is still
`unknown`, which is right for its job and wrong for a new derived column: a
notice classified before this field existed keeps its direction and never gets
an audience. Without this migration the column would fill in only as the
mirror grew, and the notices a vendor actually sees — the open ones, the
oldest-classified of all — would be the last to get it.

The classifier is imported rather than restated. It reads two plain columns
and no model, so there is nothing here to freeze; and when Q18 settles which
methods select a firm, a re-run applies the corrected answer to the archive
instead of leaving this migration asserting the old one.
"""

from __future__ import annotations

from django.db import migrations

from apps.tenders.consulting import classify_audience

BATCH = 2000


def fill_audiences(apps, schema_editor):
    TenderNotice = apps.get_model("tenders", "TenderNotice")

    # Only consulting can carry an audience, so the scan is over that slice
    # rather than the whole mirror. `.only()` keeps the notice bodies — the
    # largest column in the table — out of the read.
    queryset = (
        TenderNotice.objects.filter(category="consulting")
        .only("notice_id", "category", "procurement_method_code",
              "procurement_method_name", "consulting_audience")
        .iterator(chunk_size=BATCH)
    )

    pending = []
    for notice in queryset:
        audience = classify_audience(
            category=notice.category,
            procurement_method_code=notice.procurement_method_code,
            procurement_method_name=notice.procurement_method_name,
        ).audience
        if audience == notice.consulting_audience:
            continue
        notice.consulting_audience = audience
        pending.append(notice)
        if len(pending) >= BATCH:
            TenderNotice.objects.bulk_update(pending, ["consulting_audience"])
            pending = []

    if pending:
        TenderNotice.objects.bulk_update(pending, ["consulting_audience"])


def clear_audiences(apps, schema_editor):
    """Reverse to the field's default, which is what the column held before."""
    TenderNotice = apps.get_model("tenders", "TenderNotice")
    TenderNotice.objects.exclude(consulting_audience="").update(consulting_audience="")


class Migration(migrations.Migration):

    dependencies = [
        ("tenders", "0018_tendernotice_consulting_audience"),
    ]

    operations = [
        migrations.RunPython(fill_audiences, clear_audiences),
    ]
