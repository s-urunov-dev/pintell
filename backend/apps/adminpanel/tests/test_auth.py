"""The console is staff-only; that boundary is the point of these tests."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

User = get_user_model()

# Throttling is disabled here so repeated login attempts do not trip the
# rate limit; it is exercised in its own test below.
NO_THROTTLE = {
    "DEFAULT_THROTTLE_CLASSES": [],
    "DEFAULT_THROTTLE_RATES": {},
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.StandardResultsSetPagination",
    "PAGE_SIZE": 20,
    "EXCEPTION_HANDLER": "apps.core.exceptions.api_exception_handler",
}

PROTECTED_URL_NAMES = [
    "adminpanel:admin-me",
    "adminpanel:admin-overview",
    "adminpanel:admin-system",
    "adminpanel:admin-sync-run-list",
    "adminpanel:admin-partition-list",
    "adminpanel:admin-notice-list",
]


@override_settings(REST_FRAMEWORK=NO_THROTTLE)
class ConsoleAccessTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            username="operator", password="operator-pass-123", is_staff=True
        )
        cls.regular = User.objects.create_user(
            username="visitor", password="visitor-pass-123", is_staff=False
        )

    def test_anonymous_cannot_reach_console_endpoints(self):
        for name in PROTECTED_URL_NAMES:
            with self.subTest(endpoint=name):
                response = self.client.get(reverse(name))
                self.assertIn(
                    response.status_code,
                    (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
                )

    def test_non_staff_user_cannot_reach_console_endpoints(self):
        self.client.force_login(self.regular)
        for name in PROTECTED_URL_NAMES:
            with self.subTest(endpoint=name):
                self.assertEqual(
                    self.client.get(reverse(name)).status_code,
                    status.HTTP_403_FORBIDDEN,
                )

    def test_staff_user_can_reach_console_endpoints(self):
        self.client.force_login(self.staff)
        for name in PROTECTED_URL_NAMES:
            with self.subTest(endpoint=name):
                self.assertEqual(
                    self.client.get(reverse(name)).status_code, status.HTTP_200_OK
                )

    def test_public_api_is_unaffected_by_console_auth(self):
        # The public listing must stay anonymous and open.
        self.assertEqual(
            self.client.get(reverse("tenders:tender-list")).status_code,
            status.HTTP_200_OK,
        )

    def test_login_succeeds_for_staff(self):
        response = self.client.post(
            reverse("adminpanel:admin-login"),
            {"username": "operator", "password": "operator-pass-123"},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["user"]["username"], "operator")
        # The session is live: a protected endpoint now answers.
        self.assertEqual(
            self.client.get(reverse("adminpanel:admin-me")).status_code,
            status.HTTP_200_OK,
        )

    def test_login_rejects_wrong_password(self):
        response = self.client.post(
            reverse("adminpanel:admin-login"),
            {"username": "operator", "password": "wrong"},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["error"]["code"], "invalid_credentials")

    def test_login_rejects_unknown_user_with_the_same_message(self):
        response = self.client.post(
            reverse("adminpanel:admin-login"),
            {"username": "nobody", "password": "whatever"},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["error"]["code"], "invalid_credentials")

    def test_login_rejects_valid_non_staff_credentials(self):
        response = self.client.post(
            reverse("adminpanel:admin-login"),
            {"username": "visitor", "password": "visitor-pass-123"},
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        # Its own code, distinct from the generic `permission_denied`: "your
        # account is not staff" is a different fix from "you may not do this".
        self.assertEqual(response.data["error"]["code"], "not_staff")

    def test_inactive_staff_cannot_log_in(self):
        User.objects.filter(pk=self.staff.pk).update(is_active=False)
        response = self.client.post(
            reverse("adminpanel:admin-login"),
            {"username": "operator", "password": "operator-pass-123"},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_logout_ends_the_session(self):
        self.client.force_login(self.staff)
        self.assertEqual(
            self.client.post(reverse("adminpanel:admin-logout")).status_code,
            status.HTTP_204_NO_CONTENT,
        )
        self.assertIn(
            self.client.get(reverse("adminpanel:admin-me")).status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )

    def test_csrf_endpoint_sets_the_cookie(self):
        response = self.client.get(reverse("adminpanel:admin-csrf"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("csrftoken", response.cookies)


class LoginThrottleTests(APITestCase):
    def setUp(self):
        # DRF keeps throttle history in the cache; start from a clean slate so
        # this test neither inherits nor leaks attempts.
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_repeated_failed_logins_are_throttled(self):
        url = reverse("adminpanel:admin-login")
        statuses = {
            self.client.post(url, {"username": "x", "password": "y"}).status_code
            for _ in range(15)
        }
        self.assertIn(status.HTTP_429_TOO_MANY_REQUESTS, statuses)
