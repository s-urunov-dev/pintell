"""What `prune_out_of_scope` removes, and what it must leave standing.

The command deletes irreversibly, so the cases that matter are the negative
ones: a country stored under an alternative upstream spelling survives, a
mirrored file shared with a surviving notice is not unlinked from disk, and
nothing at all happens while the ingest gate is off.
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.tenders.models import (
    BackfillPartition,
    ContractAward,
    HarvestedDocument,
    ProjectProfile,
    TenderNotice,
)


def make_notice(notice_id: str, country: str, project_id: str = "") -> TenderNotice:
    return TenderNotice.objects.create(
        notice_id=notice_id,
        country=country,
        project_id=project_id,
        notice_date=timezone.now().date(),
        last_synced_at=timezone.now(),
    )


@override_settings(INGEST_FOCUS_ONLY=True, FOCUS_COUNTRY_GROUP="cis_plus")
class PruneOutOfScopeTests(TestCase):
    def prune(self, *args: str) -> str:
        out = StringIO()
        call_command("prune_out_of_scope", *args, stdout=out, stderr=out)
        return out.getvalue()

    def test_dry_run_reports_without_deleting(self):
        make_notice("OP-OUT", "Bangladesh")
        output = self.prune()

        self.assertIn("Dry run", output)
        self.assertEqual(TenderNotice.objects.count(), 1)

    def test_out_of_scope_notices_and_their_awards_go(self):
        keep = make_notice("OP-IN", "Uzbekistan")
        drop = make_notice("OP-OUT", "Bangladesh")
        ContractAward.objects.create(notice=drop, supplier_name="Elsewhere Ltd")
        ContractAward.objects.create(notice=keep, supplier_name="Local Ltd")

        self.prune("--apply")

        self.assertEqual(
            list(TenderNotice.objects.values_list("pk", flat=True)), ["OP-IN"]
        )
        self.assertEqual(
            list(ContractAward.objects.values_list("supplier_name", flat=True)),
            ["Local Ltd"],
        )

    def test_alternative_spelling_of_a_focus_country_survives(self):
        make_notice("OP-KG", "Kyrgyzstan")
        self.prune("--apply")
        self.assertTrue(TenderNotice.objects.filter(pk="OP-KG").exists())

    def test_a_document_still_attached_to_a_surviving_notice_stays(self):
        keep = make_notice("OP-IN", "Uzbekistan")
        drop = make_notice("OP-OUT", "Bangladesh")
        shared = HarvestedDocument.objects.create(url="https://x/tor.pdf", url_hash="a")
        lost = HarvestedDocument.objects.create(url="https://x/other.pdf", url_hash="b")
        shared.notices.add(keep, drop)
        lost.notices.add(drop)

        self.prune("--apply")

        self.assertTrue(HarvestedDocument.objects.filter(pk=shared.pk).exists())
        self.assertFalse(HarvestedDocument.objects.filter(pk=lost.pk).exists())

    def test_the_blob_is_kept_when_another_row_shares_the_hash(self):
        """Storage is content-addressed: two rows can be one file on disk."""
        blob = Path(self.mkblob())
        drop = make_notice("OP-OUT", "Bangladesh")
        keep = make_notice("OP-IN", "Uzbekistan")
        orphan = HarvestedDocument.objects.create(
            url="https://x/a.pdf", url_hash="a", sha256="dup", stored_path=str(blob)
        )
        twin = HarvestedDocument.objects.create(
            url="https://x/b.pdf", url_hash="b", sha256="dup", stored_path=str(blob)
        )
        orphan.notices.add(drop)
        twin.notices.add(keep)

        self.prune("--apply")

        self.assertTrue(blob.exists())

    def test_orphan_blob_is_removed_from_disk(self):
        blob = Path(self.mkblob())
        drop = make_notice("OP-OUT", "Bangladesh")
        document = HarvestedDocument.objects.create(
            url="https://x/a.pdf", url_hash="a", sha256="only", stored_path=str(blob)
        )
        document.notices.add(drop)

        self.prune("--apply")

        self.assertFalse(blob.exists())

    def test_project_profile_survives_while_a_notice_still_names_it(self):
        ProjectProfile.objects.create(project_id="P1", country="Uzbekistan")
        ProjectProfile.objects.create(project_id="P2", country="Bangladesh")
        make_notice("OP-IN", "Uzbekistan", project_id="P1")
        make_notice("OP-OUT", "Bangladesh", project_id="P2")

        self.prune("--apply")

        self.assertEqual(
            list(ProjectProfile.objects.values_list("pk", flat=True)), ["P1"]
        )

    def test_backfill_partitions_for_dropped_countries_are_removed(self):
        make_notice("OP-OUT", "Bangladesh")
        BackfillPartition.objects.create(key="country:Bangladesh", label="Bangladesh")
        BackfillPartition.objects.create(key="country:Uzbekistan", label="Uzbekistan")

        self.prune("--apply")

        self.assertEqual(
            list(BackfillPartition.objects.values_list("key", flat=True)),
            ["country:Uzbekistan"],
        )

    @override_settings(INGEST_FOCUS_ONLY=False)
    def test_refuses_to_delete_while_the_ingest_gate_is_off(self):
        """Otherwise the next sync restores everything this deleted."""
        make_notice("OP-OUT", "Bangladesh")
        output = self.prune("--apply")

        self.assertIn("INGEST_FOCUS_ONLY is off", output)
        self.assertEqual(TenderNotice.objects.count(), 1)

    @override_settings(FOCUS_COUNTRY_GROUP="does_not_exist")
    def test_refuses_to_run_for_an_unknown_group(self):
        """An empty allow-list would mean "delete the whole mirror"."""
        make_notice("OP-OUT", "Bangladesh")
        output = self.prune("--apply")

        self.assertIn("refusing to run", output)
        self.assertEqual(TenderNotice.objects.count(), 1)

    # -- helper -------------------------------------------------------------
    def mkblob(self) -> str:
        import tempfile

        handle = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        handle.write(b"%PDF-1.4 test")
        handle.close()
        self.addCleanup(lambda: Path(handle.name).unlink(missing_ok=True))
        return handle.name
