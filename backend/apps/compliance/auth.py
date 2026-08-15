"""Vendor accounts: register, sign in, sign out, and who am I.

Separate from the operator console's login (``apps/adminpanel``) even though
both use Django sessions, because they answer to different rules. The console
refuses any account that is not staff; this one refuses any account that *is*,
so an operator's credentials cannot be used to walk in through the public door
and a vendor can never reach operator tooling by guessing a URL.

**Why accounts at all.** A compliance verdict is computed against what a vendor
declares about itself — turnover, completed contracts, certificates. Before
accounts, that record was addressed by a sequential integer in the URL, which
made it readable by anyone who could count (docs/OPEN-QUESTIONS.md Q8). With an
account, the profile is not addressed at all: every endpoint reads the session.

**Email is the username.** Django's ``User.username`` is populated with the
email rather than adding a custom user model, which would mean a migration of
the operator accounts for no gain here. The email is stored in both fields so
``authenticate()`` works unchanged.
"""

from __future__ import annotations

import logging

from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import serializers, status
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import VendorProfile
from .serializers import VendorProfileSerializer

logger = logging.getLogger(__name__)

User = get_user_model()


class RegistrationSerializer(serializers.Serializer):
    """What is needed to open an account, and nothing else.

    The company name is asked for here rather than left to the profile form
    because a vendor who registers and stops has still told us who they are —
    and an empty profile with a name is a usable record, while an empty profile
    with no name is a row nobody can identify.
    """

    email = serializers.EmailField(max_length=254)
    password = serializers.CharField(max_length=256, write_only=True)
    name = serializers.CharField(max_length=255, trim_whitespace=True)
    country = serializers.CharField(
        max_length=100, required=False, allow_blank=True, trim_whitespace=True
    )

    def validate_email(self, value: str) -> str:
        # Case-insensitive: nobody thinks of Acme@x.com and acme@x.com as two
        # accounts, and letting both exist means one of them silently gets the
        # other's tenders.
        return value.strip().lower()

    def validate_password(self, value: str) -> str:
        """Django's own validators, surfaced as a field error.

        Not a hand-rolled rule set: the deployment configures
        ``AUTH_PASSWORD_VALIDATORS`` and this must honour whatever it says,
        including any changes made after this code was written.
        """
        try:
            validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages)) from exc
        return value


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=254)
    password = serializers.CharField(max_length=256, write_only=True)


@method_decorator(ensure_csrf_cookie, name="get")
class VendorCsrfView(APIView):
    """Hands the browser a CSRF cookie before it posts credentials."""

    authentication_classes = [SessionAuthentication]
    permission_classes = [AllowAny]
    throttle_classes: list = []

    def get(self, request):
        return Response({"detail": "CSRF cookie set."})


class VendorRegisterView(APIView):
    """`POST /api/compliance/auth/register/` — open an account and a profile.

    The two are created together, in one transaction. A vendor with an account
    but no profile would be a state every other endpoint has to defend against
    for no reason: there is exactly one profile per account, always.
    """

    authentication_classes = [SessionAuthentication]
    permission_classes = [AllowAny]
    throttle_scope = "vendor_auth"

    def post(self, request):
        form = RegistrationSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        data = form.validated_data

        try:
            with transaction.atomic():
                user = User.objects.create_user(
                    username=data["email"],
                    email=data["email"],
                    password=data["password"],
                )
                profile = VendorProfile.objects.create(
                    user=user,
                    name=data["name"],
                    country=data.get("country", ""),
                )
        except IntegrityError:
            # The unique constraint is the authority, not a prior existence
            # check: two simultaneous registrations would both pass the check
            # and one would then crash with a 500.
            raise serializers.ValidationError(
                {"email": ["An account with this email already exists."]}
            ) from None

        login(request, user)
        return Response(
            {"user": _user_payload(user), "profile": VendorProfileSerializer(profile).data},
            status=status.HTTP_201_CREATED,
        )


class VendorLoginView(APIView):
    """`POST /api/compliance/auth/login/` — session login for a vendor."""

    authentication_classes = [SessionAuthentication]
    permission_classes = [AllowAny]
    throttle_scope = "vendor_auth"

    def post(self, request):
        form = LoginSerializer(data=request.data)
        form.is_valid(raise_exception=True)

        email = form.validated_data["email"].strip().lower()
        user = authenticate(
            request, username=email, password=form.validated_data["password"]
        )

        # One message for "no such account", "wrong password" and "disabled".
        # Distinguishing them turns this endpoint into a way to discover which
        # companies have signed up.
        if user is None or not user.is_active:
            logger.info("Vendor login failed for %r", email)
            return Response(
                {"detail": "Email or password is incorrect."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if user.is_staff:
            # An operator account is for the console. Letting it sign in here
            # would give a staff session to a page that never expects one.
            logger.warning("Staff account %r attempted a vendor login.", email)
            return Response(
                {"detail": "This account belongs to the operator console."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Read before `login`, which rotates the session key against fixation.
        # The threads this browser started are filed under the *old* key, so
        # reading it afterwards would adopt nothing and the sidebar would empty
        # itself at the exact moment the vendor signed in.
        previous_key = request.session.session_key or ""

        login(request, user)

        # The chat threads started in this browser before signing in now belong
        # to the account. Imported here rather than at module scope: compliance
        # does not depend on the index, and a dead or absent chat table must not
        # be able to fail a login — which is also why the call cannot raise.
        from apps.rag_indexer.conversations import adopt_session  # noqa: PLC0415

        adopt_session(request, previous_key=previous_key)

        return Response({"user": _user_payload(user), "profile": _profile_payload(user)})


class VendorLogoutView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class VendorMeView(APIView):
    """`GET /api/compliance/auth/me/` — the signed-in vendor and their profile.

    Answers 200 with ``user: null`` rather than 401 when nobody is signed in.
    The frontend calls this once on boot to decide what to render, and a 401
    there is not an error to report — it is the ordinary state of a visitor who
    has not signed in yet.
    """

    authentication_classes = [SessionAuthentication]
    permission_classes = [AllowAny]

    def get(self, request):
        user = request.user
        if not user.is_authenticated or user.is_staff:
            return Response({"user": None, "profile": None})
        return Response({"user": _user_payload(user), "profile": _profile_payload(user)})


def _user_payload(user) -> dict:
    return {"id": user.pk, "email": user.email}


def _profile_payload(user):
    profile = VendorProfile.objects.filter(user=user).first()
    return VendorProfileSerializer(profile).data if profile else None
