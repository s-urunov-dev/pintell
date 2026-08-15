"""Stage-1 scope: tender direction, project documents/ESRS, contract awards.

Adds the fields and tables the focus feed needs:
* ``TenderNotice.source`` so other IFIs can share the table later,
* the category columns produced by the rule/AI classifier,
* ``ProjectProfile`` + ``ProjectDocument`` (documents and the ESRS summary),
* ``ContractAward`` (winner, prices, and the enriched supplier website).
"""

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models

CATEGORY_CHOICES = [
    ("construction", "Construction & works"),
    ("consulting", "Consulting services"),
    ("supply", "Supply of goods"),
    ("services", "Non-consulting services"),
    ("it", "IT & digital"),
    ("other", "Other"),
    ("unknown", "Not classified"),
]

CATEGORY_SOURCE_CHOICES = [
    ("rules", "Rule-based"),
    ("ai", "AI (Claude)"),
    ("manual", "Manual override"),
]


class Migration(migrations.Migration):

    dependencies = [
        ("tenders", "0003_alter_tendernotice_options"),
    ]

    operations = [
        # --- TenderNotice: source + direction ----------------------------
        migrations.AddField(
            model_name="tendernotice",
            name="source",
            field=models.CharField(
                db_index=True,
                default="worldbank",
                help_text="Which IFI published this notice.",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="tendernotice",
            name="category",
            field=models.CharField(
                choices=CATEGORY_CHOICES,
                db_index=True,
                default="unknown",
                help_text="Tender direction a company subscribes to.",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="tendernotice",
            name="category_source",
            field=models.CharField(
                blank=True,
                choices=CATEGORY_SOURCE_CHOICES,
                help_text="How the category was decided: rules, ai, or manual.",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="tendernotice",
            name="category_confidence",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="tendernotice",
            name="category_rationale",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="tendernotice",
            name="category_updated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name="tendernotice",
            index=models.Index(
                fields=["country", "notice_type", "deadline_date"],
                name="tender_focus_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="tendernotice",
            index=models.Index(
                fields=["category", "-notice_date"], name="tender_category_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="tendernotice",
            index=models.Index(fields=["project_id"], name="tender_project_idx"),
        ),
        # --- Project profile ---------------------------------------------
        migrations.CreateModel(
            name="ProjectProfile",
            fields=[
                ("project_id", models.CharField(max_length=32, primary_key=True, serialize=False)),
                ("source", models.CharField(db_index=True, default="worldbank", max_length=32)),
                ("name", models.TextField(blank=True)),
                ("country", models.CharField(blank=True, db_index=True, max_length=255)),
                ("status", models.CharField(blank=True, max_length=64)),
                ("lending_instrument", models.CharField(blank=True, max_length=255)),
                ("implementing_agency", models.TextField(blank=True)),
                ("sectors", models.JSONField(blank=True, default=list)),
                ("themes", models.JSONField(blank=True, default=list)),
                ("total_amount_display", models.CharField(blank=True, max_length=64)),
                ("total_amount_usd", models.DecimalField(
                    blank=True, decimal_places=2, max_digits=18, null=True)),
                ("commitment_amount_usd", models.DecimalField(
                    blank=True, decimal_places=2, max_digits=18, null=True)),
                ("board_approval_date", models.DateField(blank=True, null=True)),
                ("closing_date", models.DateField(blank=True, null=True)),
                ("esrs_report_no", models.CharField(blank=True, max_length=64)),
                ("esrs_date", models.DateField(blank=True, null=True)),
                ("esrs_title", models.TextField(blank=True)),
                ("esrs_pdf_url", models.URLField(blank=True, max_length=1000)),
                ("esrs_page_url", models.URLField(blank=True, max_length=1000)),
                ("documents_count", models.PositiveIntegerField(default=0)),
                ("project_url", models.URLField(blank=True, max_length=500)),
                ("fetched_at", models.DateTimeField(blank=True, null=True)),
                ("documents_fetched_at", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "project profile",
                "verbose_name_plural": "project profiles",
                "ordering": ["project_id"],
            },
        ),
        # --- Project documents -------------------------------------------
        migrations.CreateModel(
            name="ProjectDocument",
            fields=[
                ("guid", models.CharField(max_length=64, primary_key=True, serialize=False)),
                ("title", models.TextField(blank=True)),
                ("doc_type", models.CharField(blank=True, db_index=True, max_length=255)),
                ("doc_date", models.DateField(blank=True, null=True)),
                ("language", models.CharField(blank=True, max_length=64)),
                ("pdf_url", models.URLField(blank=True, max_length=1000)),
                ("text_url", models.URLField(blank=True, max_length=1000)),
                ("page_url", models.URLField(blank=True, max_length=1000)),
                ("fetched_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("project", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="documents",
                    to="tenders.projectprofile",
                )),
            ],
            options={
                "verbose_name": "project document",
                "verbose_name_plural": "project documents",
                "ordering": ["-doc_date", "title"],
            },
        ),
        migrations.AddIndex(
            model_name="projectdocument",
            index=models.Index(
                fields=["project", "-doc_date"], name="projdoc_project_date_idx"
            ),
        ),
        # --- Contract awards ---------------------------------------------
        migrations.CreateModel(
            name="ContractAward",
            fields=[
                ("notice", models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    primary_key=True,
                    related_name="award",
                    serialize=False,
                    to="tenders.tendernotice",
                )),
                ("supplier_name", models.CharField(blank=True, db_index=True, max_length=512)),
                ("supplier_reference", models.CharField(blank=True, max_length=64)),
                ("supplier_address", models.TextField(blank=True)),
                ("supplier_country", models.CharField(blank=True, db_index=True, max_length=255)),
                ("currency", models.CharField(blank=True, max_length=8)),
                ("bid_price_opening", models.DecimalField(
                    blank=True, decimal_places=2, max_digits=18, null=True)),
                ("evaluated_price", models.DecimalField(
                    blank=True, decimal_places=2, max_digits=18, null=True)),
                ("contract_price", models.DecimalField(
                    blank=True, decimal_places=2, max_digits=18, null=True)),
                ("award_date", models.DateField(blank=True, null=True)),
                ("contract_duration", models.CharField(blank=True, max_length=64)),
                ("evaluated_bidders", models.JSONField(
                    blank=True, default=list,
                    help_text="Other bidders named in the notice, as parsed.")),
                ("supplier_website", models.URLField(blank=True, max_length=500)),
                ("supplier_website_source", models.CharField(blank=True, max_length=32)),
                ("supplier_website_checked_at", models.DateTimeField(blank=True, null=True)),
                ("parsed_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("parser_version", models.PositiveSmallIntegerField(default=1)),
            ],
            options={
                "verbose_name": "contract award",
                "verbose_name_plural": "contract awards",
                "ordering": ["-award_date"],
            },
        ),
        migrations.AddIndex(
            model_name="contractaward",
            index=models.Index(fields=["supplier_name"], name="award_supplier_idx"),
        ),
        migrations.AddIndex(
            model_name="contractaward",
            index=models.Index(fields=["-award_date"], name="award_date_idx"),
        ),
    ]
