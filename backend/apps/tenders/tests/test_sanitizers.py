"""The sanitiser is the security boundary for untrusted upstream HTML."""

from django.test import SimpleTestCase

from apps.tenders.sanitizers import html_to_text, sanitize_html


class SanitizeHtmlTests(SimpleTestCase):
    def test_keeps_allowed_markup(self):
        cleaned = sanitize_html("<p>Hello <b>world</b><br><ul><li>one</li></ul></p>")
        self.assertIn("<b>world</b>", cleaned)
        self.assertIn("<li>one</li>", cleaned)

    def test_removes_script_tag_and_its_payload(self):
        cleaned = sanitize_html("<p>ok</p><script>alert('xss')</script>")
        self.assertNotIn("script", cleaned.lower())
        self.assertNotIn("alert", cleaned)

    def test_removes_event_handler_attributes(self):
        cleaned = sanitize_html('<div onclick="steal()" onmouseover="x()">text</div>')
        self.assertNotIn("onclick", cleaned.lower())
        self.assertNotIn("onmouseover", cleaned.lower())
        self.assertIn("text", cleaned)

    def test_drops_javascript_urls(self):
        cleaned = sanitize_html('<a href="javascript:alert(1)">click</a>')
        self.assertNotIn("javascript:", cleaned.lower())

    def test_keeps_safe_links(self):
        cleaned = sanitize_html('<a href="https://worldbank.org">wb</a>')
        self.assertIn("https://worldbank.org", cleaned)

    def test_strips_iframe_and_style_blocks(self):
        cleaned = sanitize_html(
            '<style>body{display:none}</style><iframe src="//evil"></iframe><p>hi</p>'
        )
        self.assertNotIn("iframe", cleaned.lower())
        self.assertNotIn("display:none", cleaned)
        self.assertIn("hi", cleaned)

    def test_strips_class_and_style_attributes(self):
        cleaned = sanitize_html("<div class='row col-sm-12' style='color:red'>x</div>")
        self.assertNotIn("class=", cleaned)
        self.assertNotIn("style=", cleaned)

    def test_img_with_onerror_is_removed(self):
        cleaned = sanitize_html('<img src=x onerror="alert(1)">')
        self.assertNotIn("onerror", cleaned.lower())
        self.assertNotIn("<img", cleaned.lower())

    def test_handles_empty_input(self):
        self.assertEqual(sanitize_html(None), "")
        self.assertEqual(sanitize_html(""), "")


class HtmlToTextTests(SimpleTestCase):
    def test_flattens_markup(self):
        self.assertEqual(html_to_text("<p>Hello   <b>world</b></p>"), "Hello world")

    def test_truncates(self):
        self.assertEqual(html_to_text("<p>abcdefghij</p>", max_length=5), "abcd…")
