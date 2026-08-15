"""Sync behaviour: idempotent upserts and tolerance of upstream failures."""

from __future__ import annotations

from django.test import TestCase, override_settings

from apps.tenders.models import SyncRun, TenderNotice
from apps.tenders.regions import group_countries
from apps.tenders.services.sync import sync_notices
from apps.tenders.services.worldbank import NoticePage, WorldBankAPIError

from .test_mapping import FULL_PAYLOAD, MINIMAL_PAYLOAD


class FakeClient:
    """Stands in for WorldBankClient; replays a scripted list of pages."""

    def __init__(self, pages):
        self._pages = pages
        self.calls = 0

    def iter_pages(self, **kwargs):
        for page in self._pages:
            self.calls += 1
            yield page


def page(payloads, offset=0, total=2):
    return NoticePage(notices=list(payloads), offset=offset, rows=100, total=total)


# The shared payloads are West African and Micronesian, which the focus group
# excludes. These cases are about upsert mechanics, not scope, so the gate is
# switched off here; `IngestScopeTests` below is what covers it.
@override_settings(INGEST_FOCUS_ONLY=False)
class SyncNoticesTests(TestCase):
    def test_creates_notices(self):
        client = FakeClient([page([FULL_PAYLOAD, MINIMAL_PAYLOAD])])
        stats = sync_notices(max_pages=1, client=client)

        self.assertEqual(stats.created, 2)
        self.assertEqual(TenderNotice.objects.count(), 2)
        self.assertEqual(stats.pages_failed, 0)

    def test_second_identical_run_writes_nothing(self):
        sync_notices(max_pages=1, client=FakeClient([page([FULL_PAYLOAD])]))
        stats = sync_notices(max_pages=1, client=FakeClient([page([FULL_PAYLOAD])]))

        self.assertEqual(stats.created, 0)
        self.assertEqual(stats.updated, 0)
        self.assertEqual(stats.unchanged, 1)
        self.assertEqual(TenderNotice.objects.count(), 1)

    def test_changed_payload_updates_existing_row(self):
        sync_notices(max_pages=1, client=FakeClient([page([FULL_PAYLOAD])]))
        changed = {**FULL_PAYLOAD, "bid_description": "Updated description"}
        stats = sync_notices(max_pages=1, client=FakeClient([page([changed])]))

        self.assertEqual(stats.updated, 1)
        self.assertEqual(TenderNotice.objects.count(), 1)
        notice = TenderNotice.objects.get(pk=FULL_PAYLOAD["id"])
        self.assertEqual(notice.bid_description, "Updated description")

    def test_failed_page_does_not_abort_the_run(self):
        client = FakeClient(
            [
                page([FULL_PAYLOAD]),
                WorldBankAPIError("upstream 503"),
                page([MINIMAL_PAYLOAD]),
            ]
        )
        stats = sync_notices(max_pages=3, client=client)

        self.assertEqual(stats.created, 2)
        self.assertEqual(stats.pages_failed, 1)
        self.assertEqual(stats.pages_fetched, 2)
        self.assertEqual(SyncRun.objects.first().status, SyncRun.Status.PARTIAL)

    def test_total_upstream_failure_is_recorded_as_failed(self):
        client = FakeClient([WorldBankAPIError("timeout"), WorldBankAPIError("timeout")])
        stats = sync_notices(max_pages=2, client=client)

        self.assertEqual(stats.pages_fetched, 0)
        self.assertEqual(TenderNotice.objects.count(), 0)
        self.assertEqual(SyncRun.objects.first().status, SyncRun.Status.FAILED)

    def test_payload_without_id_is_skipped_not_fatal(self):
        stats = sync_notices(
            max_pages=1, client=FakeClient([page([{"notice_type": "x"}, FULL_PAYLOAD])])
        )
        self.assertEqual(stats.skipped, 1)
        self.assertEqual(stats.created, 1)

    def test_duplicate_ids_within_a_page_collapse(self):
        stats = sync_notices(
            max_pages=1, client=FakeClient([page([FULL_PAYLOAD, dict(FULL_PAYLOAD)])])
        )
        self.assertEqual(stats.created, 1)
        self.assertEqual(TenderNotice.objects.count(), 1)

    def test_sync_run_audit_row_is_completed(self):
        sync_notices(max_pages=1, trigger="unit-test", client=FakeClient([page([FULL_PAYLOAD])]))
        run = SyncRun.objects.get()
        self.assertEqual(run.trigger, "unit-test")
        self.assertEqual(run.status, SyncRun.Status.SUCCESS)
        self.assertIsNotNone(run.finished_at)
        self.assertEqual(run.created_count, 1)


class IngestScopeTests(TestCase):
    """The focus group bounds what is stored, not only what is displayed.

    Deleting out-of-scope rows afterwards is not enough: both the incremental
    sync and the backfill's `recent` partition read the unfiltered upstream
    feed, so the mirror has to refuse them on the way in.
    """

    def _payload(self, notice_id: str, country: str) -> dict:
        return {**FULL_PAYLOAD, "id": notice_id, "project_ctry_name": country}

    @override_settings(INGEST_FOCUS_ONLY=True, FOCUS_COUNTRY_GROUP="cis_plus")
    def test_out_of_scope_country_is_never_written(self):
        client = FakeClient([page([
            self._payload("OP-IN", "Uzbekistan"),
            self._payload("OP-OUT", "Bangladesh"),
        ])])
        stats = sync_notices(max_pages=1, client=client)

        self.assertEqual(stats.created, 1)
        self.assertEqual(stats.out_of_scope, 1)
        self.assertEqual(
            list(TenderNotice.objects.values_list("pk", flat=True)), ["OP-IN"]
        )

    @override_settings(INGEST_FOCUS_ONLY=True, FOCUS_COUNTRY_GROUP="cis_plus")
    def test_alternative_upstream_spelling_is_kept(self):
        """'Kyrgyzstan' and 'Kyrgyz Republic' are the same country upstream."""
        client = FakeClient([page([self._payload("OP-KG", "Kyrgyzstan")])])
        stats = sync_notices(max_pages=1, client=client)

        self.assertEqual(stats.created, 1)
        self.assertEqual(stats.out_of_scope, 0)

    @override_settings(INGEST_FOCUS_ONLY=True, FOCUS_COUNTRY_GROUP="cis_plus")
    def test_the_audit_row_records_what_was_declined(self):
        sync_notices(
            max_pages=1,
            client=FakeClient([page([self._payload("OP-OUT", "Bangladesh")])]),
        )
        self.assertEqual(SyncRun.objects.get().out_of_scope_count, 1)

    @override_settings(INGEST_FOCUS_ONLY=True, FOCUS_COUNTRY_GROUP="does_not_exist")
    def test_an_unknown_group_widens_rather_than_empties_the_mirror(self):
        """A typo in the environment must not silently stop all ingestion."""
        client = FakeClient([page([self._payload("OP-OUT", "Bangladesh")])])
        stats = sync_notices(max_pages=1, client=client)

        self.assertEqual(stats.created, 1)
        self.assertEqual(stats.out_of_scope, 0)


class PeriodicSyncScopeTests(TestCase):
    """The scheduled sync asks each focus country, not the global feed.

    Reading the newest global pages and discarding what is out of scope is not
    only wasteful — it loses notices. Upstream publishes some 400 a day across
    all countries, so a focus notice can fall past the window between two runs,
    and once the country partitions report complete nothing goes back for it.
    """

    def _fake_sync(self, calls):
        from apps.tenders.services.sync import SyncStats

        def recorder(**kwargs):
            calls.append(kwargs)
            return SyncStats(pages_fetched=1, notices_seen=1, created=1)

        return recorder

    @override_settings(INGEST_FOCUS_ONLY=True, FOCUS_COUNTRY_GROUP="cis_plus")
    def test_one_filtered_request_per_focus_country(self):
        from unittest import mock

        from apps.tenders import tasks

        calls: list[dict] = []
        with mock.patch.object(tasks, "sync_notices", self._fake_sync(calls)):
            tasks.sync_procurement_notices()

        asked = [call["filters"]["project_ctry_name"] for call in calls]
        self.assertEqual(sorted(asked), sorted(group_countries("cis_plus")))
        self.assertTrue(all(not call["record_run"] for call in calls))

    @override_settings(INGEST_FOCUS_ONLY=True, FOCUS_COUNTRY_GROUP="cis_plus")
    def test_the_countries_share_one_audit_row(self):
        from unittest import mock

        from apps.tenders import tasks

        calls: list[dict] = []
        with mock.patch.object(tasks, "sync_notices", self._fake_sync(calls)):
            tasks.sync_procurement_notices(trigger="unit-test")

        run = SyncRun.objects.get()
        self.assertEqual(run.trigger, "unit-test")
        self.assertEqual(run.status, SyncRun.Status.SUCCESS)
        self.assertEqual(run.created_count, len(group_countries("cis_plus")))

    @override_settings(INGEST_FOCUS_ONLY=False)
    def test_without_the_gate_it_reads_the_global_feed_once(self):
        from unittest import mock

        from apps.tenders import tasks

        calls: list[dict] = []
        with mock.patch.object(tasks, "sync_notices", self._fake_sync(calls)):
            tasks.sync_procurement_notices()

        self.assertEqual(len(calls), 1)
        self.assertIsNone(calls[0]["filters"])

    @override_settings(INGEST_FOCUS_ONLY=True, FOCUS_COUNTRY_GROUP="cis_plus")
    def test_an_explicit_filter_is_honoured_as_given(self):
        """A console-triggered run for one country must not fan out to ten."""
        from unittest import mock

        from apps.tenders import tasks

        calls: list[dict] = []
        with mock.patch.object(tasks, "sync_notices", self._fake_sync(calls)):
            tasks.sync_procurement_notices(filters={"project_ctry_name": "Uzbekistan"})

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["filters"], {"project_ctry_name": "Uzbekistan"})
