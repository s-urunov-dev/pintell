"""The column that stops the lexical arm recomputing what it already knows.

``0022`` indexed ``to_tsvector(bid_description || notice_text_sanitized)`` as an
expression. That makes *matching* cheap — a GIN lookup — and leaves *ranking*
exactly as expensive as it was, because a functional index stores no vector to
rank with: ``ts_rank`` re-parses a full notice body per matching row. Measured
on the deployed corpus (D63): ranking a 300-row sample cost 652 ms, and the
300 was itself a bound hiding that "consulting services" matches 6,744
notices, so 4% of them were ranked and the sample was index order.

Restoring last night's dump into a throwaway container and adding this column
there: the same ranking cost **68 ms**, and ranking *all* 6,744 cost 354 ms
warm — faster than today's capped query, and correct.

**Nullable, and that is the whole point of splitting this across three
migrations.** Adding a nullable column with no default is a catalogue change
in PostgreSQL: instant, no table rewrite, no lock worth the name. The obvious
alternative — ``GENERATED ALWAYS AS (…) STORED`` — was measured on the same
staging copy at **79 seconds of ACCESS EXCLUSIVE**, and every page in this
product reads this table. This sequence trades a cleaner column definition for
not taking the product down:

    0023  add the column, empty                    (instant)
    0024  trigger, so every new write fills it     (instant)
          manage.py backfill_search_vector         (batched, online)
    0025  CREATE INDEX CONCURRENTLY                (online)
          RAG_LEXICAL_STORED_VECTOR=true           (the cutover)

The reader is switched by a setting rather than by this migration because a
half-filled column answers searches with rows missing, and "deployed" and
"backfilled" are minutes apart.

``SearchVectorField`` renders as ``tsvector``, a type name SQLite accepts
without meaning anything by it, so unlike ``0022`` this needs no vendor guard
to keep migrating on a non-PostgreSQL backend.
"""

from django.contrib.postgres.search import SearchVectorField
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("tenders", "0022_tender_fts_gin_index"),
    ]

    operations = [
        migrations.AddField(
            model_name="tendernotice",
            name="search_vector",
            field=SearchVectorField(
                null=True,
                editable=False,
                help_text=(
                    "Maintained by a database trigger, never by application code — "
                    "the sync writes through bulk_update, which calls no save(). "
                    "Null means not yet backfilled."
                ),
            ),
        ),
    ]
