"""Keep ``search_vector`` current, at the one place every write passes through.

**A trigger, and not ``Model.save()``.** D58 preferred a generated column
partly to avoid this trigger, and the cheaper-looking substitute is to fill the
column in ``save()``. That substitute does not work here, and the model says
why in its own docstring: *the sync writes through ``bulk_update``, which does
not call ``save()``*. Every one of the 25,463 notices in the mirror arrives and
is updated on that path. A ``save()`` override would add a query per single
-instance write while leaving the only write path that matters uncovered — the
opposite of the trade it appears to make.

So the invariant is enforced where no caller can route around it. The trigger
also covers the admin, the CSV import, a management command, and a DBA typing
UPDATE by hand, none of which a Python hook reaches.

**``UPDATE OF`` is load bearing.** Without it every write to the row —
``deadline_at`` on a re-sync, a category correction, a harvest flag — would
re-parse the whole notice body to compute a value that did not change. With
it, the recompute happens only when one of the two source columns is actually
in the UPDATE's target list.

It fires ``BEFORE``, so it sets ``NEW.search_vector`` on the row being written
rather than issuing a second UPDATE against it.

The expression is character-for-character the one in ``0022`` and in
``backfill_search_vector``. Three copies of a definition is two too many, and
the reason they are copies rather than a shared constant is that one lives in
PL/pgSQL, one in a Django expression and one in a raw ``UPDATE``: what keeps
them honest is the test that asserts the trigger and the backfill produce the
same value for the same row.
"""

from django.db import migrations

FUNCTION_NAME = "tender_search_vector_refresh"
TRIGGER_NAME = "tender_search_vector_trg"

CREATE = f"""
CREATE OR REPLACE FUNCTION {FUNCTION_NAME}() RETURNS trigger AS $$
BEGIN
    NEW.search_vector := to_tsvector(
        'english'::regconfig,
        COALESCE(NEW.bid_description, '') || ' ' || COALESCE(NEW.notice_text_sanitized, '')
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS {TRIGGER_NAME} ON tenders_tendernotice;

CREATE TRIGGER {TRIGGER_NAME}
BEFORE INSERT OR UPDATE OF bid_description, notice_text_sanitized
ON tenders_tendernotice
FOR EACH ROW EXECUTE FUNCTION {FUNCTION_NAME}();
"""

DROP = f"""
DROP TRIGGER IF EXISTS {TRIGGER_NAME} ON tenders_tendernotice;
DROP FUNCTION IF EXISTS {FUNCTION_NAME}();
"""


def create_trigger(apps, schema_editor):
    # Same vendor guard as 0022: the model must keep migrating on SQLite, where
    # none of this exists and none of it is needed.
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(CREATE)


def drop_trigger(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(DROP)


class Migration(migrations.Migration):

    dependencies = [
        ("tenders", "0023_tendernotice_search_vector"),
    ]

    operations = [
        migrations.RunPython(create_trigger, drop_trigger),
    ]
