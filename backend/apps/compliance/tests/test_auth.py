"""Vendor accounts: who gets in, who does not, and what they can then reach.

The rules worth testing here are the ones whose failure is silent. An account
that leaks which companies have registered looks like it works. A staff account
that can sign in through the public door looks like it works. A profile that
answers to an id in the URL looked like it worked for weeks (Q8).
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.compliance.models import VendorProfile

User = get_user_model()

NO_THROTTLE = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "EXCEPTION_HANDLER": "apps.core.exceptions.api_exception_handler",
}

GOOD = {
    "email": "bids@alpha.uz",
    "password": "correct-horse-battery-9",
    "name": "Alpha Qurilish",
    "country": "Uzbekistan",
}


@override_settings(REST_FRAMEWORK=NO_THROTTLE)
class RegistrationTests(APITestCase):
    def setUp(self):
        self.url = reverse("compliance:vendor-register")

    def test_registering_creates_an_account_a_profile_and_a_session(self):
        response = self.client.post(self.url, GOOD, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.json()["profile"]["name"], "Alpha Qurilish")
        # Signed in straight away: asking someone to log in immediately after
        # typing their password is a step that exists only for the server.
        me = self.client.get(reverse("compliance:vendor-me")).json()
        self.assertEqual(me["user"]["email"], "bids@alpha.uz")

    def test_a_profile_is_created_with_the_account_never_separately(self):
        self.client.post(self.url, GOOD, format="json")

        profile = VendorProfile.objects.get()
        self.assertEqual(profile.user.username, "bids@alpha.uz")

    def test_the_email_is_stored_lowercased(self):
        """Otherwise Acme@x.uz and acme@x.uz are two accounts for one company."""
        self.client.post(self.url, {**GOOD, "email": "Bids@Alpha.UZ"}, format="json")

        self.assertTrue(User.objects.filter(username="bids@alpha.uz").exists())

    def test_a_duplicate_email_is_refused_as_a_field_error(self):
        self.client.post(self.url, GOOD, format="json")

        response = self.client.post(self.url, GOOD, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(User.objects.count(), 1)

    def test_a_weak_password_is_refused_by_django_s_own_validators(self):
        response = self.client.post(self.url, {**GOOD, "password": "12345"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(User.objects.exists())

    def test_a_name_is_required(self):
        """A profile nobody can identify is not worth storing."""
        payload = {key: value for key, value in GOOD.items() if key != "name"}

        response = self.client.post(self.url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


@override_settings(REST_FRAMEWORK=NO_THROTTLE)
class LoginTests(APITestCase):
    def setUp(self):
        self.url = reverse("compliance:vendor-login")
        self.user = User.objects.create_user(
            username="bids@alpha.uz", password="correct-horse-battery-9"
        )
        VendorProfile.objects.create(user=self.user, name="Alpha Qurilish")

    def test_a_vendor_signs_in_and_gets_their_profile_back(self):
        response = self.client.post(
            self.url,
            {"email": "bids@alpha.uz", "password": "correct-horse-battery-9"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["profile"]["name"], "Alpha Qurilish")

    def test_a_wrong_password_and_an_unknown_email_fail_identically(self):
        """Otherwise this endpoint answers "does this company have an account"."""
        wrong = self.client.post(
            self.url, {"email": "bids@alpha.uz", "password": "nope"}, format="json"
        )
        unknown = self.client.post(
            self.url, {"email": "nobody@nowhere.uz", "password": "nope"}, format="json"
        )

        self.assertEqual(wrong.status_code, unknown.status_code)
        self.assertEqual(wrong.json()["detail"], unknown.json()["detail"])

    def test_an_operator_account_cannot_sign_in_here(self):
        """Console credentials must not produce a session on the public site."""
        User.objects.create_user(
            username="ops@pintell.uz", password="operator-pass-123", is_staff=True
        )

        response = self.client.post(
            self.url,
            {"email": "ops@pintell.uz", "password": "operator-pass-123"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_signing_out_ends_the_session(self):
        self.client.force_login(self.user)

        self.client.post(reverse("compliance:vendor-logout"))

        self.assertIsNone(self.client.get(reverse("compliance:vendor-me")).json()["user"])


@override_settings(REST_FRAMEWORK=NO_THROTTLE)
class WhoAmITests(APITestCase):
    def test_a_visitor_who_is_not_signed_in_is_a_normal_answer_not_an_error(self):
        """The frontend calls this on boot; a 401 there is noise, not news."""
        response = self.client.get(reverse("compliance:vendor-me"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.json()["user"])

    def test_a_staff_session_is_reported_as_nobody(self):
        """An operator browsing the public site is not a vendor there."""
        staff = User.objects.create_user(
            username="ops@pintell.uz", password="operator-pass-123", is_staff=True
        )
        self.client.force_login(staff)

        self.assertIsNone(self.client.get(reverse("compliance:vendor-me")).json()["user"])


@override_settings(REST_FRAMEWORK=NO_THROTTLE)
class ProfileIsolationTests(APITestCase):
    """The point of the accounts: one vendor cannot reach another's numbers."""

    def setUp(self):
        self.alpha = User.objects.create_user(username="a@x.uz", password="pass-alpha-123")
        VendorProfile.objects.create(
            user=self.alpha, name="Alpha", scalars={"annual_turnover_avg": 28_000_000}
        )
        self.beta = User.objects.create_user(username="b@x.uz", password="pass-beta-123")
        VendorProfile.objects.create(user=self.beta, name="Beta")

    def test_each_vendor_sees_only_their_own(self):
        self.client.force_login(self.beta)

        body = self.client.get(reverse("compliance:vendor-profile")).json()

        self.assertEqual(body["name"], "Beta")
        self.assertEqual(body["scalars"], {})

    def test_an_edit_cannot_be_aimed_at_another_vendor(self):
        """There is no id in the route, so there is nothing to aim."""
        self.client.force_login(self.beta)

        self.client.patch(
            reverse("compliance:vendor-profile"),
            data={"scalars": {"annual_turnover_avg": 1}},
            content_type="application/json",
        )

        alpha_profile = VendorProfile.objects.get(user=self.alpha)
        self.assertEqual(alpha_profile.scalars["annual_turnover_avg"], 28_000_000)
