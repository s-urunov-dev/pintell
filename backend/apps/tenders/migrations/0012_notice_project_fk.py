"""A real relation between a notice and its mirrored project.

The link already existed as a string: ``TenderNotice.project_id`` holds the
upstream key and every read did ``ProjectProfile.objects.filter(pk=...)`` by
hand. That works, but it costs a query per notice and it cannot be joined,
filtered or ordered on — "notices whose project has this team lead" was not
expressible.

``project_ref`` makes it a foreign key. It carries no database constraint on
purpose: notices arrive from one API and projects from another, almost always
in that order, so a constrained column would make the notice sync fail on rows
whose project has not been mirrored yet. The column is instead written only
when the profile exists, which the two writers guarantee.

The raw ``project_id`` column stays exactly as it was — it is the upstream
value, it is part of the API contract, and it is the only key a notice has
while its project is still unmirrored.
"""

import django.db.models.deletion
from django.db import migrations, models

# Projects per UPDATE. The notices side is index-backed (tender_project_idx),
# so this is about bounding transaction size on a table with ~400k rows.
CHUNK = 500


def link_existing_notices(apps, schema_editor):
    """Point every notice at its project, where that project is mirrored."""
    ProjectProfile = apps.get_model("tenders", "ProjectProfile")
    TenderNotice = apps.get_model("tenders", "TenderNotice")

    project_ids = list(
        ProjectProfile.objects.order_by("project_id").values_list("project_id", flat=True)
    )
    for start in range(0, len(project_ids), CHUNK):
        chunk = project_ids[start : start + CHUNK]
        for project_id in chunk:
            TenderNotice.objects.filter(
                project_id=project_id, project_ref__isnull=True
            ).update(project_ref_id=project_id)


def unlink_notices(apps, schema_editor):
    """Reverse: drop the links. The column itself goes with the AddField."""
    TenderNotice = apps.get_model("tenders", "TenderNotice")
    TenderNotice.objects.filter(project_ref__isnull=False).update(project_ref=None)


class Migration(migrations.Migration):

    dependencies = [
        ("tenders", "0011_project_refresh_state"),
    ]

    operations = [
        migrations.AddField(
            model_name="tendernotice",
            name="project_ref",
            field=models.ForeignKey(
                blank=True,
                db_constraint=False,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="notices",
                to="tenders.projectprofile",
            ),
        ),
        migrations.RunPython(link_existing_notices, unlink_notices),
    ]
