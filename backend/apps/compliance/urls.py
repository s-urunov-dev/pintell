"""URL routes for the compliance app."""

from django.urls import path

from .auth import (
    VendorCsrfView,
    VendorLoginView,
    VendorLogoutView,
    VendorMeView,
    VendorRegisterView,
)
from .views import (
    DocumentFileView,
    NoticeAssessmentView,
    NoticeDeclarationsView,
    NoticeDocumentView,
    NoticeDocumentsView,
    NoticeExpertsView,
    NoticeRequirementsView,
    VendorProfileView,
)

# Produces, under /api/compliance/:
#   auth/csrf|register|login|logout|me/
#   profile/                           -> the signed-in vendor's own profile
#   notices/{notice_id}/requirements/  -> the tender's criteria (public)
#   notices/{notice_id}/experts/       -> the team it names + our people (public)
#   notices/{notice_id}/assessment/    -> this vendor against this tender
#   notices/{notice_id}/documents/     -> hand over the tender document
#
# The notice routes are nested under the notice rather than hung off the
# profile because a requirement set belongs to a tender and exists before any
# vendor does — a vendor is the argument, not the owner. The profile route has
# no id at all: see `views.VendorSessionMixin`.
urlpatterns = [
    path("auth/csrf/", VendorCsrfView.as_view(), name="vendor-csrf"),
    path("auth/register/", VendorRegisterView.as_view(), name="vendor-register"),
    path("auth/login/", VendorLoginView.as_view(), name="vendor-login"),
    path("auth/logout/", VendorLogoutView.as_view(), name="vendor-logout"),
    path("auth/me/", VendorMeView.as_view(), name="vendor-me"),
    path("profile/", VendorProfileView.as_view(), name="vendor-profile"),
    path(
        "notices/<str:notice_id>/requirements/",
        NoticeRequirementsView.as_view(),
        name="notice-requirements",
    ),
    # The team the tender names, and who we hold for those roles. Public and
    # separate from the requirements route: the two answer different questions
    # about the same notice, and one of them reaches a verdict while the other
    # deliberately never does (D20).
    path(
        "notices/<str:notice_id>/experts/",
        NoticeExpertsView.as_view(),
        name="notice-experts",
    ),
    # The vendor answering criteria about themselves. Separate from the
    # assessment route because it writes and that one does not, and because a
    # toggle is the vendor's claim while an assessment is our arithmetic.
    path(
        "notices/<str:notice_id>/declarations/",
        NoticeDeclarationsView.as_view(),
        name="notice-declarations",
    ),
    path(
        "notices/<str:notice_id>/assessment/",
        NoticeAssessmentView.as_view(),
        name="notice-assessment",
    ),
    # The other direction: a vendor supplying the document the notice did not
    # link. Under the notice rather than under the document corpus because a
    # submission is always *for* a tender — a TOR with no tender attached is
    # a file nobody asked for.
    path(
        "notices/<str:notice_id>/documents/",
        NoticeDocumentsView.as_view(),
        name="notice-documents",
    ),
    # The split view's own payload: the document to show, its line index, and
    # where each criterion's quote sits in it. Singular where the route above
    # is plural, and the difference is real — that one is the corpus of what a
    # notice has, this one is the single file the viewer opens.
    path(
        "notices/<str:notice_id>/document/",
        NoticeDocumentView.as_view(),
        name="notice-document",
    ),
    # The bytes. Under the document rather than under a notice, because a
    # document is shared between the notices of a project and its identity is
    # its own — the same reason `HarvestedDocument` is keyed by URL.
    path(
        "documents/<str:document_id>/file/",
        DocumentFileView.as_view(),
        name="document-file",
    ),
]
