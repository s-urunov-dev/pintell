"""Drop what the deleted `apps.sourcing` left behind.

The sourcing app — a Serper search per role, a Clay verification pass, and an
admin queue that promoted a candidate into `experts.Expert` — was removed. The
directory is curated by hand now.

Deleting an app deletes its migrations with it, so nothing else would ever drop
its two tables: they would sit in every existing database, unreferenced by any
model, holding the one kind of row this project was careful about — names of
real people that a search proposed and no human approved. That is the reason
this runs as a migration rather than being left to a manual `DROP`: the
deployments that have those rows are exactly the ones nobody would remember to
clean.

The `django_migrations` rows go too. Without that, `showmigrations` reports an
app that no longer exists, and a future app reusing the label would inherit its
history.

This is deliberately irreversible. The reverse of dropping a candidate queue is
re-running the searches that filled it, which costs money and is a decision, not
a rollback.
"""

from __future__ import annotations

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("experts", "0001_initial"),
    ]

    operations = [
        # IF EXISTS because a database created after the app was deleted never
        # had them: the same migration must apply to both.
        migrations.RunSQL(
            sql=[
                "DROP TABLE IF EXISTS sourcing_expertcandidate;",
                "DROP TABLE IF EXISTS sourcing_sourcingrun;",
                "DELETE FROM django_migrations WHERE app = 'sourcing';",
            ],
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
