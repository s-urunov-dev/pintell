"""A GIN index over the notice text, so the keyword arm can run every time.

``rag_indexer.services.search`` used to describe its full-text path as
acceptably slow *because it was the fallback*: a sequential scan with a
``to_tsvector`` per row, reached only when the vector path could not answer.
Hybrid retrieval (D58) runs that same path on every question, which turns an
acceptable cost into one paid tens of thousands of times a day.

This is the index that pays for it. It is written as raw SQL rather than a
``GinIndex`` in ``Meta`` for one reason: the model must keep migrating on a
non-PostgreSQL backend, and ``django.contrib.postgres`` indexes do not. The
vendor check below is what makes ``manage.py migrate`` on SQLite a no-op here
instead of an error.

**The planner uses it — measured, not assumed.** Django passes the text-search
configuration as a bind parameter while this index states it as a literal
``'english'::regconfig``, so whether the two match at plan time was a real
question. ``EXPLAIN`` on the exact queryset ``_fts_search`` builds, against the
25,463-notice development mirror:

    Limit → Sort → Bitmap Heap Scan on tenders_tendernotice
              → Bitmap Index Scan on tender_fts_gin_idx

If a future Django changes how ``SearchVector`` renders and that becomes a Seq
Scan again, the lexical arm is slow rather than wrong — which is why this
migration was safe to ship before the check came back, and why the check is
worth re-running after a framework upgrade.

Concurrency: built without ``CONCURRENTLY`` because a migration runs inside a
transaction and cannot use it. On this table (about 25,000 rows) the build is
seconds, and it happens once at deploy.
"""

from django.db import migrations

INDEX_NAME = "tender_fts_gin_idx"

CREATE_INDEX = f"""
CREATE INDEX IF NOT EXISTS {INDEX_NAME}
ON tenders_tendernotice
USING GIN (
    to_tsvector(
        'english'::regconfig,
        COALESCE(bid_description, '') || ' ' || COALESCE(notice_text_sanitized, '')
    )
);
"""

DROP_INDEX = f"DROP INDEX IF EXISTS {INDEX_NAME};"


def create_index(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(CREATE_INDEX)


def drop_index(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(DROP_INDEX)


class Migration(migrations.Migration):

    dependencies = [
        ("tenders", "0021_alter_contractaward_currency"),
    ]

    operations = [
        migrations.RunPython(create_index, drop_index),
    ]
