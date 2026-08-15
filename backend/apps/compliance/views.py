"""The compliance API: what a tender requires, and whether one vendor meets it.

Three endpoints, and one rule that shapes all of them — **nothing here decides
anything**. The view loads stored requirements, hands them and a portfolio to
``expressions.assess``, and serialises what comes back. Every verdict in a
response was produced by arithmetic in a module with no Django import, which is
what makes it reproducible outside this application (D5).

Four behaviours are worth stating, because each rejects an easier alternative.

**A requirement whose quote could not be found never reaches a bidder.** Those
rows exist to measure hallucination (D4); they are counted in ``excluded`` so a
thin assessment cannot masquerade as a complete one, and their content is not
sent.

**A malformed requirement is skipped, not raised.** ``parse_requirement`` is a
trust boundary over JSON written by an extraction pipeline. One bad tree must
cost the reader that one criterion, not the whole page — the same contract the
harvester and the enrichment path already follow.

**A notice with nothing extracted returns an empty assessment, not a 404 and
not a 500.** ``status`` is then ``unrated``, which is the honest answer: we
have not read this tender, so we are not claiming the vendor passes or fails.
Note that ``hard_eligibility_pass`` is vacuously ``true`` in that case — an
empty conjunction — so a client must read ``status`` before rendering the
boolean. It is left as the engine computes it rather than being rewritten here,
because two places computing eligibility differently is exactly the failure the
architecture exists to avoid.

**A profile is never addressed, only inhabited.** There is no
``/profiles/{id}/`` and no list: a vendor reads and writes their own profile
through ``/profile/``, resolved from the session. The earlier design took a
sequential id in the URL, which meant one vendor's declared finances were
readable by anyone who could count — recorded as docs/OPEN-QUESTIONS.md Q8 and now
closed. Deletion is still absent, for an unrelated reason: the retention policy
is an open legal question recorded on ``VendorProfile.consented_at``, and an
endpoint that destroys data under a policy nobody has written yet is worse than
no endpoint.

**Reading a tender is public; being assessed against it is not.** The
requirements endpoint stays open — those are the borrower's published criteria
and a vendor should be able to read them before deciding to sign up. The
assessment and the document upload require a session, because both involve a
particular vendor's own data.
"""

from __future__ import annotations

import io
import logging

from django.http import FileResponse
from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.tenders.models import HarvestedDocument, TenderNotice

from apps.core.i18n import resolve_language

from apps.tenders.services import intake

from . import pipeline, reporting, scoring, viewer
from .expressions import (
    Bid,
    ExpressionError,
    Portfolio,
    assess_with_declarations,
    parse_requirement,
)
from .models import (
    ComplianceScore,
    RequirementDeclaration,
    TenderExpertPosition,
    TenderRequirement,
    VendorProfile,
)
from .serializers import (
    DeclarationWriteSerializer,
    TenderExpertPositionSerializer,
    TenderRequirementSerializer,
    VendorProfileSerializer,
)

logger = logging.getLogger(__name__)

#: Deeper layers read more of the tender, so where two layers extracted the
#: same criterion the deeper one is the better-informed statement of it.
_LAYER_DEPTH = {
    TenderRequirement.Layer.L1: 1,
    TenderRequirement.Layer.L2: 2,
    TenderRequirement.Layer.L3: 3,
}

#: How many people the notice page offers per role before sending the reader to
#: the directory. Five is a shortlist someone reads; a full list belongs behind
#: ``/api/experts/?role=…``, where it paginates.
_CANDIDATES_PER_ROLE = 5

#: Sort order for the criteria a vendor is shown: what decides the bid first.
#:
#: Built from the scoring vocabulary rather than written out, so a level cannot
#: be given a weight without also being given a place in the list. An unjudged
#: row sorts where ``scoring.DEFAULT_LEVEL`` sorts, which is the same fallback
#: its weight uses — a criterion must not appear above the gates while counting
#: for less than them.
_IMPORTANCE_RANK = {
    level: index for index, level in enumerate(scoring.IMPORTANCE_LEVELS)
}
_DEFAULT_RANK = _IMPORTANCE_RANK[scoring.DEFAULT_LEVEL]


class VendorSessionMixin:
    """Session authentication, and the signed-in vendor's own profile.

    ``get_profile`` never takes an identifier from the request. That is the
    whole point: there is no parameter to tamper with, so no endpoint below can
    be talked into reading somebody else's declaration.
    """

    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def get_profile(self) -> VendorProfile:
        profile = VendorProfile.objects.filter(user=self.request.user).first()
        if profile is None:
            # Registration creates both in one transaction, so this is either a
            # staff account reaching a vendor endpoint or a profile deleted by
            # hand. Neither is something to paper over with an empty profile.
            raise NotFound("This account has no vendor profile.")
        return profile


class VendorProfileView(VendorSessionMixin, APIView):
    """`GET`/`PATCH /api/compliance/profile/` — the signed-in vendor's own.

    PATCH rather than PUT because a profile is filled in over time, a field at
    a time, and a vendor who answers two questions should not have the other
    twelve blanked for not being mentioned.
    """

    def get(self, request):
        return Response(VendorProfileSerializer(self.get_profile()).data)

    def patch(self, request):
        profile = self.get_profile()
        form = VendorProfileSerializer(profile, data=request.data, partial=True)
        form.is_valid(raise_exception=True)
        form.save()
        return Response(form.data)


class NoticeRequirementsView(APIView):
    """`GET /api/compliance/notices/{notice_id}/requirements/`.

    What the tender asks for, with no bidder involved. Useful on its own — a
    vendor reads the criteria before deciding whether to declare anything — and
    it is also the payload that says *how* each criterion was obtained, so a
    reader can weigh a rule fired over a one-line notice differently from one
    parsed out of the tender document itself.
    """

    def get(self, request, notice_id: str):
        notice = _get_notice(notice_id)
        rows, excluded = _requirement_rows(notice)
        return Response(
            {
                "notice": _notice_payload(notice),
                "requirements": TenderRequirementSerializer(
                    rows, many=True, context={"lang": resolve_language(request)}
                ).data,
                "excluded": excluded,
                # What the reader can do when the list is thin, which is the
                # common case: three notices in four state no criteria (D12).
                # Without this the UI can only show an empty list, and an empty
                # list reads as "this tender asks for nothing" — the opposite
                # of the truth, which is that the asking is in a document
                # nobody has yet obtained.
                "documents": _document_state(notice),
            }
        )


class NoticeExpertsView(APIView):
    """`GET /api/compliance/notices/{notice_id}/experts/`.

    The two halves of one question, kept visibly apart in the payload:

    * ``positions`` — the expert roles **this tender's own text asks for**, each
      with the sentence that asks for it. Extracted, quoted, and verifiable.
    * ``candidates`` — the people **our directory holds** for those roles. A
      suggestion. Nobody has assessed them and nothing about them was read from
      the tender.

    That separation is the endpoint's whole design. Merging them into one list
    of "recommended experts for this tender" would take a hand-curated row and a
    quoted requirement and present them as the same kind of claim, which is
    precisely the confusion the expert directory was kept out of the compliance
    app to prevent (D19). A client is free to render them together; it cannot
    accidentally confuse them, because they arrive as different fields.

    Public, like the requirements endpoint and for the same reason: this is what
    the tender demands, and a vendor is entitled to read it before signing up.
    """

    def get(self, request, notice_id: str):
        notice = _get_notice(notice_id)
        positions, excluded = _expert_positions(notice)
        return Response(
            {
                "notice": _notice_payload(notice),
                "positions": TenderExpertPositionSerializer(positions, many=True).data,
                "excluded": excluded,
                "candidates": _candidates(positions),
            }
        )


class NoticeDocumentView(APIView):
    """`GET /api/compliance/notices/{notice_id}/document/`.

    The tender document beside the criteria, its line index, and which lines
    each criterion's quote sits on — everything the split view draws.

    Public, like the requirements endpoint it accompanies and for the same
    reason: this is the borrower's published document and the sentences it
    contains, which a vendor is entitled to read before signing up for
    anything. Note the difference from the *file* endpoint below, which is not
    unconditionally public.

    One request rather than three. A viewer holding spans and no file has
    nothing to draw on, one holding a file and no locations is a PDF in an
    iframe, and locations fetched separately from the index they refer to can
    be computed against a document that has since been re-harvested.
    """

    def get(self, request, notice_id: str):
        notice = _get_notice(notice_id)
        rows, _ = _requirement_rows(notice)
        return Response(viewer.payload_for(notice, rows))


class DocumentFileView(APIView):
    """`GET /api/compliance/documents/{document_id}/file/` — the bytes.

    Served from this application rather than by redirecting to the original
    URL, which is the entire reason the mirror exists: the borrower's link is
    usually dead by the time anyone reads the tender, and a viewer pointed at it
    would show an error page for most of the corpus.

    **A vendor's own upload is not public.** A harvested document was published
    — the notice linked it and anyone could fetch it — so anyone may read it
    here. A client-supplied one reached us because a vendor wrote to the
    borrower's contact, was sent a file, and passed it on (D17); it is not
    published anywhere, and serving it to unauthenticated callers would turn a
    private hand-over into a public mirror. The split is on ``origin`` for
    exactly that reason, and nothing about the two is otherwise different.

    ``authentication_classes`` is declared even though the permission stays
    open, and it is load-bearing rather than decorative: the project sets
    ``DEFAULT_AUTHENTICATION_CLASSES`` to an empty list, so a view that does not
    name one sees ``AnonymousUser`` for every caller — including a vendor with a
    valid session. Without this line the origin check below refuses the vendor
    their own upload, which is the one document they are certain to want.
    """

    authentication_classes = [SessionAuthentication]

    def get(self, request, document_id: str):
        document = HarvestedDocument.objects.filter(pk=document_id).first()
        if document is None:
            raise NotFound()

        if (
            document.origin == HarvestedDocument.Origin.CLIENT_SUPPLIED
            and not request.user.is_authenticated
        ):
            # 404 rather than 403: whether a particular document was ever handed
            # to us is itself something an anonymous caller has no business
            # learning, and a 403 answers that question.
            raise NotFound()

        payload = viewer.stored_bytes(document)
        if payload is None:
            raise NotFound()

        response = FileResponse(
            io.BytesIO(payload),
            content_type=document.content_type or "application/pdf",
        )
        # Inline: the viewer renders it in the page, and a download prompt in
        # the middle of a split view is the browser overriding the interface.
        response["Content-Disposition"] = "inline"
        return response


class NoticeAssessmentView(VendorSessionMixin, APIView):
    """`POST /api/compliance/notices/{notice_id}/assessment/`.

    No body: the tender is in the URL and the vendor is in the session. The
    earlier version accepted ``{"profile_id": 7}`` or a whole inline portfolio;
    both are gone, and their absence is the fix for Q8 — an endpoint that takes
    no profile identifier cannot be asked for someone else's.

    Still a POST although nothing is written. A GET would be the honest verb,
    but this is the request whose *response* contains a vendor's declared
    finances beside a verdict, and GETs are what proxies and browsers cache and
    what ends up in a shared history.
    """

    def post(self, request, notice_id: str):
        notice = _get_notice(notice_id)
        profile = self.get_profile()
        payload = assess(notice, profile, lang=resolve_language(request))
        payload["notice"] = _notice_payload(notice)
        payload["profile"] = {"id": profile.pk, "name": profile.name}
        # The same block the requirements endpoint carries, and for a sharper
        # reason here. An assessment with nothing to assess comes back
        # `unrated`, and `unrated` on its own tells a vendor only that we have
        # read nothing — not that the criteria are in a document the notice
        # names but does not link, nor who to write to for it. Without this the
        # verdict page would be a dead end exactly where the corpus is thinnest
        # (D12), which is where the vendor's own copy of the document is the
        # only way forward (D17).
        payload["documents"] = _document_state(notice)
        return Response(payload)


def assess(notice: TenderNotice, profile: VendorProfile, *, lang: str = "en") -> dict:
    """One vendor against one tender: the whole assessment, and its score stored.

    Split out of the view because two endpoints need the same answer. The
    assessment endpoint returns it; the declarations endpoint recomputes it so
    the switch a vendor just pressed can move the percentage in the same round
    trip. Before this existed the page had to press, wait for the write, then
    ask for a fresh assessment — two requests, and an indicator that lagged the
    control by both of them.

    **The score is written here rather than by the caller**, so there is no path
    that produces an assessment and forgets to update the cached figure. What is
    stored is a copy of what is being returned; see ``ComplianceScore`` for why
    a derived number is persisted at all, and why nothing ever reads it back to
    decide eligibility.
    """
    portfolio_source = profile.as_portfolio_payload()

    # One member. A joint venture is a composition of several profiles and the
    # engine already evaluates it; the UI for building one is deferred
    # (DECISIONS.md), so every bid made here is a single entity — which
    # `Bid.single` models without a special case.
    bid = Bid.single(
        Portfolio(
            name=portfolio_source["name"],
            scalars=portfolio_source["scalars"],
            collections=portfolio_source["collections"],
        )
    )

    rows, excluded = _requirement_rows(notice)
    requirements, provenance, unparsable = _parse(rows, lang=lang)
    if unparsable:
        excluded["unparsable"] = unparsable

    # What the vendor has already answered for themselves, aligned with
    # `requirements` positionally — `provenance[i]["id"]` is the row that
    # produced requirement `i`, which is the only safe way to line the two up
    # (the same key can come from two layers).
    declared = _declarations_for(profile, provenance)

    payload = reporting.assessment_payload(
        assess_with_declarations(requirements, bid, declared),
        bid,
        provenance=provenance,
        excluded=excluded,
        declarations=declared,
    )
    _store_score(profile, notice, payload["score"])
    return payload


def _store_score(profile: VendorProfile, notice: TenderNotice, score: dict) -> None:
    """Cache the figure this assessment just computed.

    Overwritten rather than appended, unlike every recorded table in this app,
    because the row is a copy of a derivable value and a history of it would be
    a history of how often somebody opened the page.

    A failure here is swallowed. The vendor asked what they qualify for and the
    answer is already computed; losing the list-view cache is not a reason to
    turn a correct assessment into a 500, and the next assessment rewrites it.
    """
    try:
        ComplianceScore.objects.update_or_create(
            profile=profile,
            notice=notice,
            defaults={
                "score": score["score"],
                "ceiling": score["ceiling"],
                "earned": score["earned"],
                "open_weight": score["open"],
                "lost": score["lost"],
                "total": score["total"],
                "requirements": score["counts"]["total"],
                "satisfied": score["counts"]["satisfied"],
                "blocked": score["blocked"],
            },
        )
    except Exception:  # noqa: BLE001 - a cache write must not fail a verdict
        logger.warning(
            "Could not store the compliance score for %s/%s",
            profile.pk, notice.pk, exc_info=True,
        )


def _declarations_for(
    profile: VendorProfile, provenance: list[dict]
) -> list[bool | None]:
    """The vendor's own answers, in requirement order.

    ``None`` for a requirement they have not answered, which is not ``False``:
    the engine still gets its turn, and the result may legitimately be UNKNOWN.
    Collapsing the two would turn every question the vendor has not reached into
    a failure.
    """
    answers = dict(
        RequirementDeclaration.objects.filter(
            profile=profile,
            requirement_id__in=[entry["id"] for entry in provenance],
        ).values_list("requirement_id", "satisfied")
    )
    return [answers.get(entry["id"]) for entry in provenance]


class NoticeDeclarationsView(VendorSessionMixin, APIView):
    """`POST /api/compliance/notices/{notice_id}/declarations/`.

    The vendor answering a criterion about themselves: yes, no, or — by sending
    ``null`` — withdrawing an answer they gave before.

    **Why a toggle exists at all.** Most of what a tender demands is not a
    number and not a file. "Advanced university degree in agricultural
    economics" is something a vendor has or has not, and the interface used to
    offer them only two ways to say so: enter a value the criterion has no field
    for, or upload a document. A vendor who did neither stayed at "not yet
    established" permanently, which described our ignorance rather than their
    eligibility — and most vendors do not upload anything.

    The body is a list, because a vendor works down a page of criteria and one
    request per switch would make the page's state depend on the order the
    network happened to deliver them in.

    Nothing here can create or alter a requirement: the ids are looked up
    against this notice's own rows and anything else is refused. A vendor's
    answer is a claim about themselves, never about what the tender says.
    """

    def post(self, request, notice_id: str):
        notice = _get_notice(notice_id)
        profile = self.get_profile()

        serializer = DeclarationWriteSerializer(data=request.data, many=True)
        serializer.is_valid(raise_exception=True)
        entries = serializer.validated_data

        # Scoped to this notice, so a stolen id from another tender cannot be
        # written through this route.
        valid_ids = set(
            TenderRequirement.objects.filter(
                notice=notice, id__in=[e["requirement_id"] for e in entries]
            ).values_list("id", flat=True)
        )
        unknown = [e["requirement_id"] for e in entries if e["requirement_id"] not in valid_ids]
        if unknown:
            raise ValidationError(
                {"requirement_id": [f"Not a requirement of {notice_id}: {unknown}"]}
            )

        cleared = [e["requirement_id"] for e in entries if e["satisfied"] is None]
        if cleared:
            RequirementDeclaration.objects.filter(
                profile=profile, requirement_id__in=cleared
            ).delete()

        for entry in entries:
            if entry["satisfied"] is None:
                continue
            RequirementDeclaration.objects.update_or_create(
                profile=profile,
                requirement_id=entry["requirement_id"],
                defaults={"satisfied": entry["satisfied"]},
            )

        stored = dict(
            RequirementDeclaration.objects.filter(profile=profile).values_list(
                "requirement_id", "satisfied"
            )
        )
        # The recomputed percentage, in the same response as the write.
        #
        # It is not decoration. The whole point of the indicator is that it
        # answers the switch, and the switch is answered by a number nobody can
        # compute on the client: it is weighted by an importance the vendor
        # cannot see and it depends on verdicts the engine reaches from the
        # whole set. Returning it here means the bar moves on the round trip
        # that saved the answer, with one implementation of the arithmetic —
        # the alternative was a second one in TypeScript, drifting.
        return Response(
            {
                "declarations": stored,
                "score": assess(
                    notice, profile, lang=resolve_language(request)
                )["score"],
            }
        )


class NoticeDocumentsView(VendorSessionMixin, APIView):
    """`POST /api/compliance/notices/{notice_id}/documents/`.

    A vendor supplies the tender document the notice did not link — either as
    an uploaded file (multipart ``file``) or as a link they were sent
    (``{"url": "..."}``, including a Google Drive or Docs share link).

    The loop this closes: most notices state no criteria because the criteria
    are in a Terms of Reference the notice only names, and the notice publishes
    a contact precisely so a bidder can ask for it. What comes back goes into
    the same corpus and is read by the same L3 (DECISIONS.md D17).

    Extraction runs inline rather than on a worker. The vendor is waiting, and
    an upload that returned "queued" would leave them with no way to tell a slow
    job from a lost one; the cost is a slow request on a large document, which
    is the right trade while a submission is a deliberate act by one person
    rather than a stream. Moving it to Celery is a change of one call.
    """

    #: Signed in *and* throttled. The session narrows who can fill the harvest
    #: volume to accounts rather than to the internet; the throttle bounds what
    #: one account can do with that, since registering is free.
    throttle_scope = "anon"

    def post(self, request, notice_id: str):
        notice = _get_notice(notice_id)

        uploaded = request.FILES.get("file")
        url = (request.data.get("url") or "").strip() if not uploaded else ""
        if not uploaded and not url:
            raise ValidationError({"detail": "Send either a file or a url."})

        kind = (request.data.get("kind") or HarvestedDocument.Kind.TOR).strip()
        if kind not in HarvestedDocument.Kind.values:
            raise ValidationError({"kind": f"Unknown document kind {kind!r}."})

        try:
            if uploaded:
                result = intake.accept_upload(
                    notice,
                    payload=uploaded.read(),
                    filename=uploaded.name or "upload",
                    kind=kind,
                )
            else:
                result = intake.accept_url(notice, url=url, kind=kind)
        except intake.IntakeRejected as exc:
            raise ValidationError({"detail": str(exc)}) from exc

        # A document that arrived but could not be read is reported as a 200
        # with the reason, not as an error: the vendor did nothing wrong, and
        # "your scan has no text layer" is something they can act on.
        payload = {
            "document": {
                "id": result.document.pk if result.document else None,
                "kind": result.document.kind if result.document else kind,
                "readable": result.readable,
                "text_chars": result.document.text_chars if result.document else 0,
                "problem": result.problem,
            },
            "extraction": None,
        }

        if result.readable:
            run = pipeline.extract_from_supplied(notice)
            if run is not None:
                payload["extraction"] = {
                    "layers": run.layers,
                    "status": run.status,
                    "requirements_found": run.requirements.count(),
                    "error": run.error,
                }

        rows, excluded = _requirement_rows(notice)
        payload["requirements"] = TenderRequirementSerializer(
            rows, many=True, context={"lang": resolve_language(request)}
        ).data
        payload["excluded"] = excluded
        return Response(payload, status=status.HTTP_201_CREATED)



# ---------------------------------------------------------------------------
# Shared loading
# ---------------------------------------------------------------------------
def _get_notice(notice_id: str) -> TenderNotice:
    notice = TenderNotice.objects.filter(pk=notice_id).only(
        "notice_id", "bid_description", "project_name", "country",
        "notice_type", "deadline_date", "procurement_method_code",
        "procurement_method_name",
    ).first()
    if notice is None:
        raise NotFound()
    return notice


def _notice_payload(notice: TenderNotice) -> dict:
    """Just enough of the notice to caption the assessment.

    The full notice already has an endpoint; repeating it here would mean two
    representations of the same record drifting apart.
    """
    return {
        "id": notice.notice_id,
        "title": notice.bid_description or notice.project_name,
        "country": notice.country,
        "notice_type": notice.notice_type,
        "deadline_date": notice.deadline_date,
        "procurement_method_code": notice.procurement_method_code,
        "procurement_method_name": notice.procurement_method_name,
    }


def _document_state(notice: TenderNotice) -> dict:
    """Which documents back this notice, and who to ask when none do.

    The contact block is only filled in when nothing readable is attached.
    Publishing a borrower's email beside a tender we *can* already read would
    send vendors to ask for something we hold, and would spread a contact
    address further than the task needs — the notice publishes it so a bidder
    can obtain the tender documents, not so every reader has it to hand.
    """
    readable = list(notice.harvested_documents.usable())
    supplied = [
        document
        for document in readable
        if document.origin == HarvestedDocument.Origin.CLIENT_SUPPLIED
    ]
    state = {
        "readable": len(readable),
        "supplied_by_vendors": len(supplied),
        "can_extract": bool(readable),
        # What is already held, per document. The counts above say how much;
        # this says which, and the interface needs it to show a vendor that the
        # slot they are about to fill is already full — the earlier panel asked
        # for a Terms of Reference without ever mentioning that it had one, so
        # vendors uploaded the same file twice and neither upload said why the
        # second changed nothing.
        "held": [
            {
                "id": document.pk,
                "kind": document.kind,
                "origin": document.origin,
                "url": document.url,
                "text_chars": document.text_chars,
                "page_count": document.page_count,
            }
            for document in readable
        ],
        "contact": None,
    }
    if readable:
        return state

    state["contact"] = {
        "name": notice.contact_name,
        "organization": notice.contact_organization,
        "email": notice.contact_email,
        "phone": notice.contact_phone_no,
        "address": notice.contact_address,
        "web_url": notice.contact_web_url,
    }
    return state


def _expert_positions(notice: TenderNotice) -> tuple[list[TenderExpertPosition], dict]:
    """The expert positions to show for one notice, and what was held back.

    The same collapse ``_requirement_rows`` performs, over a different key.
    Runs accumulate, and a notice read twice would otherwise ask a vendor for
    two Team Leaders because two runs each found one.

    **The dedupe key is the title, not the role.** A TOR asking for an
    Environmental Specialist and a Social Specialist maps both onto roles in the
    same family and might map both onto the same role; collapsing on role would
    silently drop a seat the tender actually needs filled. Two positions with
    the same title in one run are the duplicate; two titles are two people.
    """
    rows = list(
        TenderExpertPosition.objects.filter(notice=notice).select_related(
            "run", "role", "role__parent"
        )
    )

    withheld = [row for row in rows if not row.is_usable]
    usable = [row for row in rows if row.is_usable]
    usable.sort(
        key=lambda row: (row.run.created_at, _LAYER_DEPTH.get(row.layer, 0)),
        reverse=True,
    )

    chosen: dict[str, TenderExpertPosition] = {}
    superseded = 0
    for row in usable:
        fingerprint = row.title.strip().casefold()
        if fingerprint in chosen:
            superseded += 1
            continue
        chosen[fingerprint] = row

    selected = sorted(
        chosen.values(), key=lambda row: (not row.is_mandatory, row.title.casefold())
    )
    return selected, {"not_found": len(withheld), "superseded": superseded}


def _candidates(positions: list[TenderExpertPosition]) -> dict:
    """Who the directory holds for the roles this tender named.

    Keyed by role slug so a client can put each shortlist beside the position
    that produced it without matching anything back up. Positions the taxonomy
    could not file contribute no key at all — there is no role to look people up
    by, and inventing a shortlist for them would be exactly the fuzzy match the
    schema-constrained vocabulary exists to avoid (D20).

    Bounded per role. This is a hint next to a verdict, not the directory: a
    vendor who wants the full list has ``/api/experts/?role=…``, which paginates
    and sorts. Ordering is by name so the same tender shows the same people in
    the same order to everyone — a directory that reshuffled per request would
    look like a ranking nobody computed.
    """
    slugs = {position.role_id for position in positions if position.role_id}
    if not slugs:
        return {}

    from apps.experts.models import Expert  # noqa: PLC0415 - see module docstring
    from apps.experts.serializers import ExpertSerializer  # noqa: PLC0415

    shortlists: dict[str, list] = {}
    for slug in slugs:
        people = (
            Expert.objects.filter(types__slug=slug)
            .prefetch_related("types__parent")
            .order_by("full_name")[:_CANDIDATES_PER_ROLE]
        )
        shortlists[slug] = ExpertSerializer(people, many=True).data
    return shortlists


def _requirement_rows(notice: TenderNotice) -> tuple[list[TenderRequirement], dict]:
    """The requirements to show for one notice, and what was held back.

    Runs are never overwritten (``ExtractionRun`` keeps every pass so the
    ablation stays measurable), so a notice accumulates rows: a later run may
    re-state a criterion an earlier one already found, and a deeper layer may
    restate one a shallower layer guessed at. Showing all of them would put the
    same criterion in front of a bidder several times.

    So rows are collapsed by ``key``, keeping the newest run and, within a run,
    the deepest layer. This is a **display** rule, not a semantic one — it does
    not merge or reconcile two extractions, it picks one — and it is here in the
    read path rather than in the schema precisely so the extraction track can
    replace it without a migration.
    """
    rows = list(
        TenderRequirement.objects.filter(notice=notice)
        .select_related("run", "source_document")
    )

    withheld = [row for row in rows if not row.is_usable]
    usable = [row for row in rows if row.is_usable]

    # Newest run first, then deepest layer, so the first row seen for a key is
    # the one to keep.
    usable.sort(
        key=lambda row: (row.run.created_at, _LAYER_DEPTH.get(row.layer, 0)),
        reverse=True,
    )
    chosen: dict[str, TenderRequirement] = {}
    superseded = 0
    for row in usable:
        if row.key in chosen:
            superseded += 1
            continue
        chosen[row.key] = row

    # Stable, readable order for the reader: what decides the bid first, then
    # the criteria the document requires plainly, then its preferences.
    #
    # Importance leads and ``is_mandatory`` follows it rather than the other way
    # round, because the two answer different questions and the first is the one
    # a vendor is triaging on: a criterion the text says a bidder is ineligible
    # without belongs above one it merely requires, whichever way the mandatory
    # flag fell. Where both agree — the common case — the order is unchanged
    # from before.
    #
    # Still not sorted by verdict. The verdict is not known here, and a list
    # that reordered itself per bidder would make two assessments of the same
    # tender impossible to compare — and would move a row out from under the
    # vendor's cursor the moment they answered it.
    selected = sorted(
        chosen.values(),
        key=lambda row: (
            _IMPORTANCE_RANK.get(row.importance, _DEFAULT_RANK),
            not row.is_mandatory,
            row.key,
        ),
    )

    excluded = {
        # Quote not found in the source: an extraction error, not a fact about
        # the tender. Counted so the reader knows something was withheld, and
        # never described further.
        "not_found": len(withheld),
        "superseded": superseded,
    }
    return selected, excluded


def _parse(
    rows: list[TenderRequirement], *, lang: str = "en"
) -> tuple[list, list[dict], int]:
    """Stored rows as engine requirements, plus where each one came from.

    A row that will not parse is dropped and counted. The alternative — letting
    ``ExpressionError`` reach the client — would turn one bad extraction into a
    500 on a page that could still have answered most of the bidder's question.

    Provenance carries two fields the engine has no notion of, and both belong
    to the stored row rather than to the parsed expression:

    * ``label``, resolved into ``lang``. It overrides the engine's own English
      label when the payload is assembled (``reporting.requirement_payload``),
      which is the one place that substitution happens.
    * ``importance``, which orders the list and weights the score and never
      reaches a verdict (``scoring``).
    """
    requirements = []
    provenance: list[dict] = []
    unparsable = 0

    for row in rows:
        try:
            requirements.append(parse_requirement(row.as_payload()))
        except ExpressionError:
            unparsable += 1
            logger.warning(
                "Unusable requirement row id=%s notice=%s key=%s",
                row.pk, row.notice_id, row.key, exc_info=True,
            )
            continue
        provenance.append(
            {
                "id": row.pk,
                "label": row.label_for(lang),
                "importance": row.importance,
                "layer": row.layer,
                "grounding": row.grounding,
                "source_document": (
                    {
                        "id": row.source_document.pk,
                        "url": row.source_document.url,
                        "kind": row.source_document.kind,
                        "status": row.source_document.status,
                    }
                    if row.source_document_id
                    else None
                ),
                "run": {
                    "id": row.run_id,
                    "layers": row.run.layers,
                    "model": row.run.model,
                },
            }
        )

    return requirements, provenance, unparsable
