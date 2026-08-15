"""The archive walk must be resumable, partitioned, and cap-aware."""

from __future__ import annotations

from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.tenders.models import BackfillPartition, TenderNotice
from apps.tenders.regions import group_countries
from apps.tenders.services.backfill import (
    LOCK_KEY,
    LOCK_TTL,
    RECENT_KEY,
    backfill_progress,
    ensure_partitions,
    next_partition,
    run_backfill_slice,
)
from apps.tenders.services.worldbank import NoticePage, WorldBankAPIError

from .test_mapping import FULL_PAYLOAD


def payload(notice_id: str, country: str = "Kenya") -> dict:
    return {**FULL_PAYLOAD, "id": notice_id, "project_ctry_name": country}


class ScriptedClient:
    """Returns `rows` synthetic notices per page until `total` is exhausted."""

    def __init__(self, total: int, fail_offsets: set[int] | None = None, country="Kenya"):
        self.total = total
        self.fail_offsets = fail_offsets or set()
        self.country = country
        self.requested: list[tuple[int, dict]] = []

    def fetch_page(self, *, offset: int = 0, rows: int = 100, **filters):
        self.requested.append((offset, filters))
        if offset in self.fail_offsets:
            raise WorldBankAPIError(f"upstream 503 at offset {offset}")
        remaining = max(0, self.total - offset)
        count = min(rows, remaining)
        notices = [payload(f"OP{offset + i:08d}", self.country) for i in range(count)]
        return NoticePage(notices=notices, offset=offset, rows=rows, total=self.total)



# Partition planning has two modes. These cases cover discovery — the countries
# are read out of what is already mirrored — so the focus seeding is off;
# `FocusPartitionTests` below covers the other mode.
@override_settings(INGEST_FOCUS_ONLY=False)
class EnsurePartitionsTests(TestCase):
    def test_creates_recent_partition(self):
        ensure_partitions()
        self.assertTrue(BackfillPartition.objects.filter(key=RECENT_KEY).exists())

    def test_creates_one_partition_per_known_country(self):
        TenderNotice.objects.create(notice_id="A1", country="Kenya")
        TenderNotice.objects.create(notice_id="A2", country="India")
        TenderNotice.objects.create(notice_id="A3", country="India")
        ensure_partitions()

        keys = set(BackfillPartition.objects.values_list("key", flat=True))
        self.assertIn("country:Kenya", keys)
        self.assertIn("country:India", keys)
        self.assertEqual(len(keys), 3)  # recent + 2 countries

    def test_is_idempotent_and_picks_up_new_countries(self):
        TenderNotice.objects.create(notice_id="A1", country="Kenya")
        ensure_partitions()
        self.assertEqual(ensure_partitions(), 0)

        TenderNotice.objects.create(notice_id="A2", country="Peru")
        self.assertEqual(ensure_partitions(), 1)

    def test_extra_countries_can_be_seeded_explicitly(self):
        ensure_partitions(extra_countries=["Uzbekistan"])
        self.assertTrue(BackfillPartition.objects.filter(key="country:Uzbekistan").exists())

    def test_recent_partition_is_worked_first(self):
        TenderNotice.objects.create(notice_id="A1", country="Kenya")
        ensure_partitions()
        self.assertEqual(next_partition().key, RECENT_KEY)


@override_settings(INGEST_FOCUS_ONLY=True, FOCUS_COUNTRY_GROUP="cis_plus")
class FocusPartitionTests(TestCase):
    """With the ingest gate on, the focus group defines the partitions."""

    def test_a_focus_country_with_nothing_stored_still_gets_a_partition(self):
        """The bug this exists to prevent: no rows -> no partition -> no rows.

        Belarus and the Russian Federation stayed empty for exactly this
        reason while upstream held 1,783 notices for them.
        """
        ensure_partitions()

        keys = set(BackfillPartition.objects.values_list("key", flat=True))
        self.assertIn("country:Belarus", keys)
        self.assertIn("country:Russian Federation", keys)

    def test_every_focus_country_is_covered(self):
        ensure_partitions()

        countries = set(
            BackfillPartition.objects.filter(
                kind=BackfillPartition.Kind.COUNTRY
            ).values_list("label", flat=True)
        )
        self.assertEqual(countries, set(group_countries("cis_plus")))

    def test_seeding_is_idempotent(self):
        ensure_partitions()
        self.assertEqual(ensure_partitions(), 0)

    def test_the_unfiltered_walk_is_not_created(self):
        """It would spend 1 000 upstream requests to re-fetch what the country
        partitions already cover, and being picked first it would delay them."""
        ensure_partitions()

        self.assertFalse(BackfillPartition.objects.filter(key=RECENT_KEY).exists())

    def test_a_recent_partition_left_from_before_the_gate_is_not_chosen(self):
        """The state the server was actually in: `recent` half-walked and
        RUNNING, nine country partitions untouched behind it.

        It is RUNNING with a non-zero offset, so it matches the in-progress
        branch too — the walk would have stayed on it for 3 800 more pages
        while the focus countries stayed empty.
        """
        ensure_partitions()
        BackfillPartition.objects.create(
            key=RECENT_KEY,
            kind=BackfillPartition.Kind.RECENT,
            label="Newest notices (unfiltered)",
            filters={},
            status=BackfillPartition.Status.RUNNING,
            next_offset=30_000,
            upstream_total=414_197,
        )

        for _ in range(len(group_countries("cis_plus"))):
            chosen = next_partition()
            self.assertNotEqual(chosen.key, RECENT_KEY)
            # Retire it so the next call has to make a fresh choice.
            chosen.status = BackfillPartition.Status.COMPLETED
            chosen.save(update_fields=["status"])

        # Every country retired and still never `recent`: the walk reports the
        # archive done rather than falling back to the global feed.
        self.assertIsNone(next_partition())

    def test_the_walk_resumes_from_its_checkpoint_when_the_gate_comes_off(self):
        """Why the row is skipped and not deleted."""
        BackfillPartition.objects.create(
            key=RECENT_KEY,
            kind=BackfillPartition.Kind.RECENT,
            label="Newest notices (unfiltered)",
            filters={},
            status=BackfillPartition.Status.RUNNING,
            next_offset=30_000,
        )

        with override_settings(INGEST_FOCUS_ONLY=False):
            chosen = next_partition()

        self.assertEqual(chosen.key, RECENT_KEY)
        self.assertEqual(chosen.next_offset, 30_000)


# Off for the same reason as in test_sync: these payloads are out-of-scope
# countries and the cases are about checkpointing, not about the scope gate.
@override_settings(INGEST_FOCUS_ONLY=False)
class RunBackfillSliceTests(TestCase):
    def test_walks_a_partition_and_checkpoints(self):
        client = ScriptedClient(total=250)
        result = run_backfill_slice(max_pages=2, rows_per_page=100, client=client, page_delay=0)

        self.assertEqual(result.partition_key, RECENT_KEY)
        self.assertEqual(result.pages_done, 2)
        self.assertEqual(result.created, 200)
        self.assertFalse(result.finished_partition)

        partition = BackfillPartition.objects.get(key=RECENT_KEY)
        self.assertEqual(partition.next_offset, 200)
        self.assertEqual(partition.upstream_total, 250)
        self.assertEqual(partition.status, BackfillPartition.Status.RUNNING)

    def test_resumes_from_the_checkpoint(self):
        client = ScriptedClient(total=250)
        run_backfill_slice(max_pages=1, rows_per_page=100, client=client, page_delay=0)
        run_backfill_slice(max_pages=1, rows_per_page=100, client=client, page_delay=0)

        self.assertEqual([offset for offset, _ in client.requested], [0, 100])
        self.assertEqual(TenderNotice.objects.count(), 200)

    def test_completes_when_upstream_total_is_reached(self):
        client = ScriptedClient(total=150)
        result = run_backfill_slice(max_pages=5, rows_per_page=100, client=client, page_delay=0)

        self.assertTrue(result.finished_partition)
        self.assertEqual(TenderNotice.objects.count(), 150)
        partition = BackfillPartition.objects.get(key=RECENT_KEY)
        self.assertEqual(partition.status, BackfillPartition.Status.COMPLETED)
        self.assertIsNotNone(partition.finished_at)

    def test_failed_page_keeps_the_checkpoint_for_a_retry(self):
        client = ScriptedClient(total=500, fail_offsets={100})
        result = run_backfill_slice(max_pages=5, rows_per_page=100, client=client, page_delay=0)

        self.assertEqual(result.pages_done, 1)
        self.assertEqual(result.pages_failed, 1)
        partition = BackfillPartition.objects.get(key=RECENT_KEY)
        # Offset stays at the failed page so the next run picks it up again.
        self.assertEqual(partition.next_offset, 100)
        self.assertIn("503", partition.last_error)
        self.assertFalse(partition.is_done)

    def test_country_partition_sends_the_country_filter(self):
        TenderNotice.objects.create(notice_id="SEED", country="Kenya")
        ensure_partitions()
        BackfillPartition.objects.filter(key=RECENT_KEY).update(
            status=BackfillPartition.Status.COMPLETED
        )

        client = ScriptedClient(total=100)
        result = run_backfill_slice(max_pages=1, rows_per_page=100, client=client, page_delay=0)

        self.assertEqual(result.partition_key, "country:Kenya")
        self.assertEqual(client.requested[0][1], {"project_ctry_name": "Kenya"})

    def test_specific_partition_can_be_targeted(self):
        ensure_partitions(extra_countries=["Peru"])
        client = ScriptedClient(total=100, country="Peru")
        result = run_backfill_slice(
            max_pages=1, rows_per_page=100, client=client,
            partition_key="country:Peru", page_delay=0,
        )
        self.assertEqual(result.partition_key, "country:Peru")

    def test_unknown_partition_is_rejected(self):
        with self.assertRaises(ValueError):
            run_backfill_slice(partition_key="country:Atlantis", client=ScriptedClient(total=1))

    def test_idle_once_every_partition_is_done(self):
        ensure_partitions()
        BackfillPartition.objects.update(status=BackfillPartition.Status.COMPLETED)

        result = run_backfill_slice(client=ScriptedClient(total=100), page_delay=0)
        self.assertTrue(result.idle)
        self.assertEqual(result.idle_reason, "complete")
        self.assertEqual(result.pages_done, 0)

    def test_concurrent_slices_are_locked_out(self):
        cache.add(LOCK_KEY, "held-by-another-worker", 60)
        try:
            result = run_backfill_slice(client=ScriptedClient(total=500), page_delay=0)
        finally:
            cache.delete(LOCK_KEY)

        self.assertTrue(result.idle)
        self.assertEqual(result.idle_reason, "locked")
        self.assertEqual(TenderNotice.objects.count(), 0)

    def test_lock_lease_is_short_so_a_killed_slice_self_heals(self):
        # A hard kill skips the explicit release; the lease must expire fast
        # enough that the archive walk resumes on its own.
        self.assertLessEqual(LOCK_TTL, 300)

    def test_lease_is_renewed_while_pages_are_processed(self):
        run_backfill_slice(
            max_pages=3, rows_per_page=100,
            client=ScriptedClient(total=1000), page_delay=0,
        )
        # Released at the end of the slice, never left behind.
        self.assertIsNone(cache.get(LOCK_KEY))

    def test_lock_is_released_after_a_slice(self):
        run_backfill_slice(
            max_pages=1, rows_per_page=100,
            client=ScriptedClient(total=500), page_delay=0,
        )
        self.assertIsNone(cache.get(LOCK_KEY))

    @override_settings(
        WORLDBANK={
            **{
                "API_URL": "https://example.invalid",
                "ROWS_PER_PAGE": 100,
                "MAX_PAGES": 5,
                "HTTP_TIMEOUT": 5,
                "USER_AGENT": "test",
                "NOTICE_DETAIL_URL": "https://example.invalid/{id}",
                "ATTRIBUTION": "test",
                "BACKFILL_PAGES_PER_RUN": 5,
                "BACKFILL_PAGE_DELAY": 0,
                "BACKFILL_INTERVAL_MINUTES": 5,
                # Tiny cap so the subdivision path is exercised cheaply.
                "MAX_OFFSET": 200,
            }
        }
    )
    def test_country_over_the_offset_cap_is_subdivided(self):
        TenderNotice.objects.create(notice_id="SEED", country="India")
        ensure_partitions()
        BackfillPartition.objects.filter(key=RECENT_KEY).update(
            status=BackfillPartition.Status.COMPLETED
        )

        client = ScriptedClient(total=5000, country="India")
        run_backfill_slice(
            max_pages=10, rows_per_page=100, client=client,
            partition_key="country:India", page_delay=0,
        )

        partition = BackfillPartition.objects.get(key="country:India")
        self.assertEqual(partition.status, BackfillPartition.Status.SUBDIVIDED)
        self.assertEqual(partition.next_offset, 200)  # stopped at the cap
        children = BackfillPartition.objects.filter(
            kind=BackfillPartition.Kind.COUNTRY_METHOD
        )
        self.assertGreater(children.count(), 0)
        self.assertEqual(
            children.first().filters.get("project_ctry_name"), "India"
        )


# Counts partitions, so the focus seeding is off: these are about the
# percentage arithmetic, not about which partitions exist.
@override_settings(INGEST_FOCUS_ONLY=False)
class BackfillProgressTests(TestCase):
    def test_progress_reports_coverage(self):
        ensure_partitions(extra_countries=["Kenya"])
        BackfillPartition.objects.filter(key="country:Kenya").update(
            upstream_total=1000, next_offset=500
        )
        BackfillPartition.objects.filter(key=RECENT_KEY).update(
            upstream_total=1000, next_offset=1000, status=BackfillPartition.Status.COMPLETED
        )

        progress = backfill_progress()
        self.assertEqual(progress["partitions_total"], 2)
        self.assertEqual(progress["partitions_completed"], 1)
        self.assertEqual(progress["rows_walked"], 1500)
        self.assertEqual(progress["rows_reachable_known"], 2000)
        # Measured in partitions: 1 of 2 finished.
        self.assertEqual(progress["percent"], 50.0)
        self.assertFalse(progress["complete"])

    def test_percent_is_not_inflated_by_undiscovered_partitions(self):
        # Pending partitions have no known total yet; a row-based percentage
        # would read 100% here, which is what this guards against.
        ensure_partitions(extra_countries=["Kenya", "Peru", "Chile"])
        BackfillPartition.objects.filter(key=RECENT_KEY).update(
            upstream_total=100, next_offset=100,
            status=BackfillPartition.Status.COMPLETED,
        )

        progress = backfill_progress()
        self.assertEqual(progress["partitions_completed"], 1)
        self.assertEqual(progress["partitions_total"], 4)
        self.assertEqual(progress["percent"], 25.0)
        self.assertFalse(progress["complete"])
