"""GIN over the stored column, built without taking the table offline.

``0022`` said it built its index without ``CONCURRENTLY`` "because a migration
runs inside a transaction and cannot use it", and at seconds on a 25,000-row
table that was the right call. It is not the right call twice: this runs on a
live server that is now the product's only deployment, and the difference
between a SHARE lock and no lock is the difference between writes queueing for
the length of the build and not.

``atomic = False`` is what buys that — Django wraps each migration in a
transaction unless told otherwise, and ``CREATE INDEX CONCURRENTLY`` is exactly
the statement PostgreSQL refuses to run inside one.

**Built before the backfill, deliberately.** At this point the column is almost
entirely NULL, so the build is trivial and the rows the backfill writes
afterwards maintain the index incrementally as ordinary updates. Doing it the
other way round — backfill first, index second — means one large build over a
full column instead of many small maintenance writes, for no benefit.

``IF NOT EXISTS`` because a ``CONCURRENTLY`` build that fails partway leaves an
``INVALID`` index behind rather than nothing, and re-running the migration
should not then fail on a name clash. An invalid index is reported by:

    SELECT indexrelid::regclass FROM pg_index WHERE NOT indisvalid;

and is dropped and rebuilt by hand — which is the one part of this that a
migration should not do for you.

**The old functional index from 0022 is left in place.** It is what serves
search until ``RAG_LEXICAL_STORED_VECTOR`` is switched on, and dropping it in
the same deploy that adds this one would mean a window with no usable index at
all. Dropping it is a separate, deliberate step after the cutover — see
``deploy/README.md``.
"""

from django.db import migrations

INDEX_NAME = "tender_search_vector_gin_idx"

CREATE_INDEX = f"""
CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX_NAME}
ON tenders_tendernotice USING GIN (search_vector);
"""

DROP_INDEX = f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME};"


def create_index(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(CREATE_INDEX)


def drop_index(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(DROP_INDEX)


class Migration(migrations.Migration):
    # See the module docstring: CONCURRENTLY cannot run inside a transaction.
    atomic = False

    dependencies = [
        ("tenders", "0024_tender_search_vector_trigger"),
    ]

    operations = [
        migrations.RunPython(create_index, drop_index),
    ]
