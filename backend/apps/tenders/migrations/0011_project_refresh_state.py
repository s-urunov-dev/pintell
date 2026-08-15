"""Bookkeeping that lets a mirrored project be fetched a second time.

Until now a profile was mirrored once and never looked at again — the sync
queue was built as "every focus project_id that has no row yet", so the row
itself was the marker for "done". That made a failure permanent: a failed
attempt still left a row behind, and the row said done.

Three columns fix it. ``last_attempt_at`` moves on every try (``fetched_at``
stays the last *successful* one), ``error_count`` counts consecutive failures,
and ``next_retry_at`` is when the backoff derived from it expires.

The data step seeds rows that already carry a ``last_error``: they are exactly
the ones the old queue had written off, so they start with one failure against
them and a retry due immediately.
"""

from django.db import migrations, models
from django.db.models.functions import Now


def seed_failed_profiles(apps, schema_editor):
    """Existing failures get one strike and an immediate retry."""
    ProjectProfile = apps.get_model("tenders", "ProjectProfile")
    ProjectProfile.objects.exclude(last_error="").update(
        error_count=1, next_retry_at=Now()
    )


def clear_retry_state(apps, schema_editor):
    """Reverse: drop the retry schedule; the columns go with the AddFields."""
    ProjectProfile = apps.get_model("tenders", "ProjectProfile")
    ProjectProfile.objects.update(error_count=0, next_retry_at=None)


class Migration(migrations.Migration):

    dependencies = [
        ("tenders", "0010_teamleadprofile_bank_page_checked_at_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="projectprofile",
            name="error_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="projectprofile",
            name="last_attempt_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="projectprofile",
            name="next_retry_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddIndex(
            model_name="projectprofile",
            index=models.Index(fields=["fetched_at"], name="projprof_fetched_idx"),
        ),
        migrations.RunPython(seed_failed_profiles, clear_retry_state),
    ]
