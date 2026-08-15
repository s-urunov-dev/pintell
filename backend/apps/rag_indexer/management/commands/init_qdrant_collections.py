"""Create both Qdrant collections up front, instead of on first use.

    python manage.py init_qdrant_collections            # create what is missing
    python manage.py init_qdrant_collections --status    # report, change nothing

Two collections, and the separation is a decision rather than a detail (D57):
the archive's passages, which the chat may cite, and the answer cache, which it
may never cite. One collection with a flag on each point is one forgotten
filter away from a chat answer citing a chat answer.

**This reverses half of D57's lazy creation, on purpose.** That decision had
the cache collection created on its first *write* — "a deployment that never
answers a question should not create a collection". The argument is sound and
the consequence was that on the deployed server the collection simply did not
exist, weeks after the feature shipped, and nothing anywhere said whether that
meant "not used yet" or "broken". A collection that exists and is empty answers
that question; a collection that is absent does not.

What is kept from D57 is the half that matters: **creation never happens on the
read path.** A lookup that created a collection would turn a dead Qdrant into a
write on the critical path of every cache miss. This command, and the
non-fatal call to it in the container entrypoint, are both outside any request.

Degrades like everything else that talks to Qdrant. A dead store makes this
report the fact and exit 0 — the entrypoint calls it on every web container
start, and a vector store that is down must not stop the API from serving. The
index is a cache (D43); nothing in the product waits on it.
"""

from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.rag_indexer.services.cache import SemanticCache
from apps.rag_indexer.services.qdrant import (
    QdrantService,
    QdrantUnavailable,
    get_qdrant_service,
)


class Command(BaseCommand):
    help = "Create the archive and answer-cache Qdrant collections if they are missing."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--status", action="store_true",
            help="Report which collections exist and exit.",
        )

    def _targets(self) -> list[tuple[str, QdrantService]]:
        """The archive store, and the cache's own store with its own indexes.

        The cache's `QdrantService` is built the way `SemanticCache` builds it
        rather than by hand, so the payload indexes cannot drift from the keys
        the cache actually filters on.
        """
        return [
            ("archive", get_qdrant_service()),
            ("answer cache", SemanticCache()._store),
        ]

    def handle(self, *args, **options) -> None:
        if not settings.RAG["ENABLED"]:
            self.stdout.write("RAG_ENABLED is off — nothing to create.")
            return

        status_only = options["status"]

        for label, store in self._targets():
            name = store.collection
            try:
                if status_only:
                    stats = store.stats()
                    if not stats.connected:
                        state = f"unreachable — {stats.error or 'no answer'}"
                    elif not stats.exists:
                        # The distinction this command exists to make legible.
                        state = "missing"
                    else:
                        state = f"{stats.points} points"
                    self.stdout.write(f"{label:<13} {name:<22} {state}")
                    continue

                created = store.ensure_collection()
                verb = "created" if created else "already present"
                self.stdout.write(
                    self.style.SUCCESS(f"{label:<13} {name:<22} {verb}")
                )
            except QdrantUnavailable as exc:
                # Deliberately not an error exit. See the module docstring: the
                # entrypoint runs this, and a dead vector store may not stop a
                # container that serves tenders perfectly well without it.
                self.stdout.write(
                    self.style.WARNING(f"{label:<13} {name:<22} skipped — {exc}")
                )
            except Exception as exc:  # noqa: BLE001
                self.stdout.write(
                    self.style.WARNING(f"{label:<13} {name:<22} skipped — {exc}")
                )
