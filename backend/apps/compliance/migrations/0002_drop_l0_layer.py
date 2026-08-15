"""Drop the L0 layer and the `exempt` grounding state (DECISIONS.md D17).

Choices-only, so nothing is rewritten on disk. There is no data migration
because there is nothing to migrate: L0 was never implemented, so no row ever
carried `layer="L0"` or `grounding="exempt"` — the states were declared ahead of
a layer that was then cancelled. Should a row somehow exist, `layer` would
simply fail model validation rather than be silently rewritten to something
else, which is the correct outcome for a value that no longer means anything.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("compliance", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="tenderrequirement",
            name="layer",
            field=models.CharField(
                choices=[
                    ("L1", "Deterministic rule over the notice body"),
                    ("L2", "LLM extraction from the notice body"),
                    ("L3", "Parsed from a tender document"),
                ],
                db_index=True,
                max_length=2,
            ),
        ),
        migrations.AlterField(
            model_name="tenderrequirement",
            name="grounding",
            field=models.CharField(
                choices=[
                    ("verified", "Quote found in source"),
                    ("not_found", "Quote not found — do not use"),
                    ("unchecked", "Not verified yet"),
                ],
                default="unchecked",
                max_length=12,
            ),
        ),
    ]
