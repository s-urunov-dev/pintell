"""Indexes for the filters the API actually issues.

The public filters are ``iexact`` (so ``?country=uzbekistan`` works), which
compiles to ``UPPER(col) = UPPER(%s)`` — a predicate no plain column index can
serve, so every filtered list ended up scanning the whole archive. The
matching expression indexes are added here.

``-last_synced_at`` is the operator console's default ordering and had no
index at all.

The ordering indexes that need ``NULLS LAST`` live in 0006: SQLite does not
accept that keyword inside an index definition.
"""

import django.db.models.functions.text
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenders', '0004_focus_projects_awards'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='tendernotice',
            index=models.Index(django.db.models.functions.text.Upper('notice_type'), name='tender_type_upper_idx'),
        ),
        migrations.AddIndex(
            model_name='tendernotice',
            index=models.Index(django.db.models.functions.text.Upper('procurement_method_code'), name='tender_meth_upper_idx'),
        ),
        migrations.AddIndex(
            model_name='tendernotice',
            index=models.Index(django.db.models.functions.text.Upper('procurement_method_name'), name='tender_methnm_upper_idx'),
        ),
        migrations.AddIndex(
            model_name='tendernotice',
            index=models.Index(fields=['-last_synced_at'], name='tender_synced_idx'),
        ),
    ]
