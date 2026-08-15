"""Language negotiation and the localised error envelope.

The contract these lock down: a client that asks for a language gets error
*messages* in it, while the machine-readable ``code`` never changes.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.core.i18n import (
    DEFAULT_LANGUAGE,
    MESSAGES,
    SUPPORTED_LANGUAGES,
    normalize_language,
    parse_accept_language,
    translate,
)

User = get_user_model()

NO_THROTTLE = {
    "DEFAULT_THROTTLE_CLASSES": [],
    "DEFAULT_THROTTLE_RATES": {},
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.StandardResultsSetPagination",
    "PAGE_SIZE": 20,
    "EXCEPTION_HANDLER": "apps.core.exceptions.api_exception_handler",
}


class CatalogueTests(SimpleTestCase):
    def test_uzbek_is_the_default(self):
        self.assertEqual(DEFAULT_LANGUAGE, "uz")

    def test_every_message_exists_in_every_language(self):
        for code, entry in MESSAGES.items():
            for language in SUPPORTED_LANGUAGES:
                with self.subTest(code=code, language=language):
                    self.assertTrue(
                        entry.get(language, "").strip(),
                        f"{code} has no {language} translation",
                    )

    def test_the_three_languages_differ(self):
        """A copy-pasted English string would silently defeat the feature."""
        for code, entry in MESSAGES.items():
            with self.subTest(code=code):
                self.assertNotEqual(entry["uz"], entry["en"])
                self.assertNotEqual(entry["ru"], entry["en"])

    def test_placeholders_match_across_languages(self):
        import re

        pattern = re.compile(r"\{(\w+)")
        for code, entry in MESSAGES.items():
            expected = set(pattern.findall(entry["en"]))
            for language in SUPPORTED_LANGUAGES:
                with self.subTest(code=code, language=language):
                    self.assertEqual(set(pattern.findall(entry[language])), expected)

    def test_translate_interpolates(self):
        message = translate("unknown_partition", "ru", partition="country:India")
        self.assertIn("country:India", message)

    def test_translate_returns_none_for_an_unknown_code(self):
        self.assertIsNone(translate("no_such_code", "uz"))

    def test_translate_survives_a_missing_placeholder(self):
        # A caller that forgets a parameter must not turn an error into a 500.
        self.assertIsInstance(translate("unknown_partition", "uz"), str)


class LanguageNegotiationTests(SimpleTestCase):
    def test_normalize_strips_the_region(self):
        self.assertEqual(normalize_language("ru-RU"), "ru")
        self.assertEqual(normalize_language("UZ"), "uz")

    def test_normalize_rejects_unsupported(self):
        self.assertIsNone(normalize_language("de-DE"))
        self.assertIsNone(normalize_language(""))
        self.assertIsNone(normalize_language(None))

    def test_accept_language_honours_quality(self):
        self.assertEqual(parse_accept_language("en;q=0.4, ru;q=0.9"), "ru")

    def test_accept_language_skips_unsupported_entries(self):
        self.assertEqual(parse_accept_language("de, fr, uz"), "uz")

    def test_accept_language_without_a_match(self):
        self.assertIsNone(parse_accept_language("de-DE, fr-FR"))


@override_settings(REST_FRAMEWORK=NO_THROTTLE)
class ErrorEnvelopeLanguageTests(APITestCase):
    """A 404 on the public API is the simplest end-to-end error path."""

    def url(self):
        return reverse("tenders:tender-detail", kwargs={"notice_id": "no-such-notice"})

    def test_defaults_to_uzbek(self):
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data["error"]["code"], "not_found")
        self.assertEqual(response.data["error"]["language"], "uz")
        self.assertEqual(response.data["error"]["message"], MESSAGES["not_found"]["uz"])

    def test_accept_language_selects_russian(self):
        response = self.client.get(self.url(), HTTP_ACCEPT_LANGUAGE="ru-RU,ru;q=0.9")
        self.assertEqual(response.data["error"]["message"], MESSAGES["not_found"]["ru"])

    def test_query_parameter_overrides_the_header(self):
        response = self.client.get(
            self.url(), {"lang": "en"}, HTTP_ACCEPT_LANGUAGE="ru-RU"
        )
        self.assertEqual(response.data["error"]["message"], MESSAGES["not_found"]["en"])

    def test_unsupported_language_falls_back_to_uzbek(self):
        response = self.client.get(self.url(), HTTP_ACCEPT_LANGUAGE="de-DE")
        self.assertEqual(response.data["error"]["message"], MESSAGES["not_found"]["uz"])

    def test_the_code_is_language_independent(self):
        codes = {
            self.client.get(self.url(), {"lang": language}).data["error"]["code"]
            for language in SUPPORTED_LANGUAGES
        }
        self.assertEqual(codes, {"not_found"})


@override_settings(REST_FRAMEWORK=NO_THROTTLE)
class ConsoleErrorLanguageTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.visitor = User.objects.create_user(
            username="visitor", password="visitor-pass-123", is_staff=False
        )
        cls.staff = User.objects.create_user(
            username="operator", password="operator-pass-123", is_staff=True
        )

    def test_bad_credentials_are_localised(self):
        response = self.client.post(
            reverse("adminpanel:admin-login"),
            {"username": "operator", "password": "wrong"},
            HTTP_ACCEPT_LANGUAGE="ru",
        )
        self.assertEqual(response.data["error"]["code"], "invalid_credentials")
        self.assertEqual(
            response.data["error"]["message"], MESSAGES["invalid_credentials"]["ru"]
        )

    def test_non_staff_login_is_localised(self):
        response = self.client.post(
            reverse("adminpanel:admin-login"),
            {"username": "visitor", "password": "visitor-pass-123"},
        )
        self.assertEqual(response.data["error"]["code"], "not_staff")
        self.assertEqual(response.data["error"]["message"], MESSAGES["not_staff"]["uz"])

    def test_staff_only_endpoints_report_the_staff_requirement(self):
        self.client.force_login(self.visitor)
        response = self.client.get(reverse("adminpanel:admin-overview"), {"lang": "ru"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["error"]["code"], "staff_required")
        self.assertEqual(
            response.data["error"]["message"], MESSAGES["staff_required"]["ru"]
        )

    def test_field_errors_carry_a_code_and_a_localised_message(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("adminpanel:admin-login"), {}, HTTP_ACCEPT_LANGUAGE="ru"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        details = response.data["error"]["details"]
        self.assertEqual(details["username"][0]["code"], "required")
        self.assertEqual(details["username"][0]["message"], MESSAGES["field.required"]["ru"])

    def test_unknown_partition_names_the_key(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("adminpanel:admin-trigger-backfill"),
            {"partition_key": "country:Atlantis"},
            HTTP_ACCEPT_LANGUAGE="uz",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        message = response.data["error"]["details"]["partition_key"][0]["message"]
        self.assertIn("country:Atlantis", message)
