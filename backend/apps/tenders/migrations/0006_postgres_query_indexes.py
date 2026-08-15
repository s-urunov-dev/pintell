"""Indexes only PostgreSQL can express (the production backend).

Two kinds:

**Ordering.** ``TenderNotice.Meta.ordering`` sorts by ``notice_date DESC NULLS
LAST`` — part of the archive predates the upstream ``noticedate`` field, and
PostgreSQL would otherwise float those undated rows to the top of "newest
first". A plain ``-notice_date`` index cannot answer that ordering, so every
list request sorted the whole table. These two match the ORDER BY term for
term, including the ``notice_id`` tie-breaker, and the country variant covers
the site's most common query: one country, newest first. SQLite rejects
``NULLS LAST`` inside a CREATE INDEX, which is why they are not in
``Meta.indexes``.

**Search.** ``?search=`` is a DRF ``SearchFilter``: it becomes ``ILIKE
'%term%'`` ORed across several columns. No btree index can answer a leading
wildcard, so each search scanned the archive twice (pagination counts the same
rows first). A GIN index over trigrams makes those matches indexable — and
every branch of the OR needs one, because a single unindexed column sends the
planner back to a sequential scan of the whole condition.

On any other backend every operation below is a no-op, so the SQLite dev/test
path keeps working exactly as before: correct results, scanned not indexed.
"""

from django.db import migrations

TABLE = "tenders_tendernotice"

# Ordering indexes: (expression, index name).
ORDERING_INDEXES = [
    (
        '"notice_date" DESC NULLS LAST, "notice_id" DESC',
        "tender_order_idx",
    ),
    (
        'UPPER("country"), "notice_date" DESC NULLS LAST, "notice_id" DESC',
        "tender_ctry_order_idx",
    ),
]

# The columns DRF searches (see TenderNoticeViewSet.search_fields) plus the
# country column the Django admin searches.
TRIGRAM_INDEXES = {
    "bid_description": "tender_desc_trgm_idx",
    "project_name": "tender_projname_trgm_idx",
    "bid_reference_no": "tender_bidref_trgm_idx",
    "project_id": "tender_projid_trgm_idx",
    "contact_organization": "tender_contactorg_trgm_idx",
    "country": "tender_country_trgm_idx",
}


class PostgresOnlyRunSQL(migrations.RunSQL):
    """``RunSQL`` that quietly does nothing on a non-PostgreSQL backend."""

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor != "postgresql":
            return
        super().database_forwards(app_label, schema_editor, from_state, to_state)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor != "postgresql":
            return
        super().database_backwards(app_label, schema_editor, from_state, to_state)


def _drop(index_name: str) -> str:
    return f"DROP INDEX IF EXISTS {index_name};"


class Migration(migrations.Migration):
    dependencies = [
        ("tenders", "0005_query_indexes"),
    ]

    operations = [
        *[
            PostgresOnlyRunSQL(
                sql=(
                    f"CREATE INDEX IF NOT EXISTS {name} ON {TABLE} ({expression});"
                ),
                reverse_sql=_drop(name),
            )
            for expression, name in ORDERING_INDEXES
        ],
        PostgresOnlyRunSQL(
            sql="CREATE EXTENSION IF NOT EXISTS pg_trgm;",
            # The extension may be shared with the rest of the database;
            # dropping it on reverse would reach further than this migration.
            reverse_sql=migrations.RunSQL.noop,
        ),
        *[
            PostgresOnlyRunSQL(
                sql=(
                    f"CREATE INDEX IF NOT EXISTS {name} ON {TABLE} "
                    f'USING gin ("{column}" gin_trgm_ops);'
                ),
                reverse_sql=_drop(name),
            )
            for column, name in TRIGRAM_INDEXES.items()
        ],
    ]
