from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenders', '0019_backfill_consulting_audience'),
    ]

    operations = [
        migrations.AlterField(
            model_name='tendernotice',
            name='category_source',
            field=models.CharField(blank=True, choices=[('rules', 'Rule-based'), ('ai', 'AI (Claude)'), ('manual', 'Manual override'), ('agent', 'Agent review')], help_text='How the category was decided: rules, ai, agent, or manual.', max_length=10),
        ),
    ]
