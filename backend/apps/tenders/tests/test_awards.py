"""Contract Award parsing — the competitor-analysis data path."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from apps.tenders.models import ContractAward, TenderNotice
from apps.tenders.services.awards import parse_award_text, parse_notice_award, parse_pending_awards
from apps.tenders.services.ai import enrichment

# A real upstream award body, shortened.
AWARD_HTML = (
    "<div><h4>Contract Award</h4>"
    "<p><b>Project:</b>P174085-Recovery and Advancement of Informal Sector Employment<br>"
    "<b>Bid/Contract Reference No:</b>GD-037<br>"
    "<b>Procurement Method:</b>RFB-Request for Bids</p>"
    "<p>Date Notification of Award Issued<br>(YYYY/MM/DD)<br>2025/06/22<br>"
    "Duration of Contract<br>30 Day(s)</p>"
    "<p>Awarded Bidder(s):<br>"
    "GLORY OFFICE SOLUTION (746232)<br>"
    "67 Motijheel BA/A (4th Floor) Dhaka-1000<br>"
    "Country: Bangladesh</p>"
    "<p>Bid Price at Opening<br>BDT<br>"
    "Evaluated Bid Price<br>BDT 15952213.00<br>"
    "Signed Contract price<br>BDT 15952213.00</p></div>"
)

# Notice OP00461079, cut to its section structure. Every awkward feature here
# is upstream's, not invented for the test: the winner is a joint venture, the
# ownership disclosure sits between the winner and its rivals, and each
# bidder's prices are printed before the next bidder's name.
MULTI_BIDDER_HTML = (
    "<div><h4>Contract Award</h4>"
    "<p>Date Notification of Award Issued<br>(YYYY/MM/DD)<br>2026/06/17<br>"
    "Duration of Contract<br>540 Day(s)</p>"
    "<p>Awarded Bidder(s):<br>"
    "METAG INSAAT TICARET (851176)<br>"
    "Konutkent Mah.3028.Cad. No : 6/29 CANKAYA/ANKARA/TURKIYE<br>"
    "Country: Turkiye<br>"
    '"SHAXRISABZ YASHIL DIYOR" LLC (1066189)<br>'
    "262, Nasaf Dacha street, Karshi city, Kashkadarya region.<br>"
    "Country: Uzbekistan<br>"
    "Evaluation Scores<br>"
    "Bid Price at Opening<br>USD<br>"
    "Evaluated Bid Price<br>USD 9613518.63<br>"
    "Signed Contract price<br>USD 9613518.63</p>"
    "<p>Beneficial Ownership Details<br>"
    "METAG INSAAT TICARET (851176)<br>"
    "Form Date: 06-AUG-26<br>Name<br>Nationality<br>Residence Country<br>"
    "Ahmet Munir Agca<br>TR<br>Turkiye</p>"
    "<p>Evaluated Bidder(s):<br>"
    "2-SON MKK (882180)<br>"
    "Uzbekistan, Samarkand region<br>"
    "Country: Uzbekistan<br>"
    "FOROUZANDEH CONSTRUCTION COMPANY (1066186)<br>"
    "5th floor, Hajj Boulevard, Kio Square<br>"
    "Country: Iran, Islamic Republic of<br>"
    "Evaluation Scores<br>"
    "Bid Price at Opening<br>USD<br>"
    "Evaluated Bid Price<br>USD 9014065.00</p>"
    "<p>Rejected Bidder(s):<br>"
    "FAYZ BINOKOR OLTIARIQ LLC (1057031)<br>"
    "528, Tashkent str., Bekobod district, Tashkent region<br>"
    "Country: Uzbekistan<br>"
    "Evaluation Scores<br>"
    "Bid Price at Opening<br>USD<br>"
    "Evaluated Bid Price<br>USD<br>"
    "Reason for Rejection<br>Non Responsive</p></div>"
)


class ParseAwardTextTests(SimpleTestCase):
    def setUp(self):
        self.details = parse_award_text(AWARD_HTML)

    def test_extracts_the_winner(self):
        self.assertEqual(self.details.supplier_name, "GLORY OFFICE SOLUTION")
        self.assertEqual(self.details.supplier_reference, "746232")
        self.assertEqual(self.details.supplier_country, "Bangladesh")
        self.assertIn("Motijheel", self.details.supplier_address)

    def test_extracts_prices_and_currency(self):
        self.assertEqual(self.details.currency, "BDT")
        self.assertEqual(self.details.evaluated_price, Decimal("15952213.00"))
        self.assertEqual(self.details.contract_price, Decimal("15952213.00"))

    def test_extracts_award_date_and_duration(self):
        self.assertEqual(self.details.award_date, date(2025, 6, 22))
        self.assertIn("30", self.details.contract_duration)

    def test_details_are_usable(self):
        self.assertTrue(self.details.is_useful)

    def test_named_bidder_layout(self):
        details = parse_award_text(
            "<p>Awarded Bidder(s):<br>Name: Alke Insaat Sanayi ve Ticaret A.S.<br>"
            "Address:<br>Country: Turkey<br>"
            "Bid Price at Opening: USD 7,384,272.25<br>"
            "Evaluated Bid Price: USD 7,384,272.25</p>"
        )
        self.assertEqual(details.supplier_name, "Alke Insaat Sanayi ve Ticaret A.S.")
        self.assertEqual(details.supplier_country, "Turkey")
        self.assertEqual(details.bid_price_opening, Decimal("7384272.25"))

    def test_evaluated_bidders_are_collected(self):
        details = parse_award_text(
            "<p>Awarded Bidder(s):<br>Name: Winner Ltd<br>Country: Uzbekistan<br>"
            "Evaluated Bidder(s):<br>Name: JV Runner Up Ltd<br>Country: Kazakhstan</p>"
        )
        self.assertEqual(details.supplier_name, "Winner Ltd")
        self.assertEqual(len(details.evaluated_bidders), 1)
        self.assertEqual(details.evaluated_bidders[0]["name"], "JV Runner Up Ltd")

    def test_empty_body_is_not_useful(self):
        self.assertFalse(parse_award_text("").is_useful)
        self.assertFalse(parse_award_text("<p>Contract Award</p>").is_useful)


class BidderSectionTests(SimpleTestCase):
    """The three bidder lists, read off the layout upstream actually publishes.

    ``MULTI_BIDDER_HTML`` is notice OP00461079 reduced to its structure: a
    joint-venture winner, a beneficial-ownership section between the winner
    and its rivals, and bidders whose prices are printed between them rather
    than after them all.
    """

    def setUp(self):
        self.details = parse_award_text(MULTI_BIDDER_HTML)

    def test_a_joint_venture_winner_keeps_both_members(self):
        names = [b["name"] for b in self.details.awarded_bidders]
        self.assertEqual(names, ["METAG INSAAT TICARET", '"SHAXRISABZ YASHIL DIYOR" LLC'])
        self.assertEqual(self.details.awarded_bidders[1]["reference"], "1066189")

    def test_the_flat_columns_carry_the_first_member_only(self):
        self.assertEqual(self.details.supplier_name, "METAG INSAAT TICARET")
        self.assertEqual(self.details.supplier_country, "Turkiye")
        self.assertIn("Konutkent", self.details.supplier_address)
        # The co-member used to be appended here, which turned every joint
        # venture's address into a second company.
        self.assertNotIn("SHAXRISABZ", self.details.supplier_address)

    def test_beneficial_ownership_is_not_read_as_a_bidder_list(self):
        every_name = [
            bidder["name"]
            for group in (
                self.details.awarded_bidders,
                self.details.evaluated_bidders,
                self.details.rejected_bidders,
            )
            for bidder in group
        ]
        self.assertNotIn("Ahmet Munir Agca", every_name)

    def test_every_evaluated_bidder_is_collected_not_just_the_first(self):
        names = [b["name"] for b in self.details.evaluated_bidders]
        self.assertEqual(names, ["2-SON MKK", "FOROUZANDEH CONSTRUCTION COMPANY"])
        self.assertEqual(
            self.details.evaluated_bidders[1]["country"], "Iran, Islamic Republic of"
        )

    def test_rejected_bidders_are_kept_apart_with_their_reason(self):
        rejected = self.details.rejected_bidders
        self.assertEqual([b["name"] for b in rejected], ["FAYZ BINOKOR OLTIARIQ LLC"])
        self.assertEqual(rejected[0]["rejection_reason"], "Non Responsive")

    def test_interleaved_prices_still_reach_the_winner(self):
        # The award's own prices sit inside the awarded section, so a section
        # parser that swallowed them would have emptied every contract value.
        self.assertEqual(self.details.currency, "USD")
        self.assertEqual(self.details.contract_price, Decimal("9613518.63"))
        # A losing bidder's cheaper price must not overwrite the winner's.
        self.assertEqual(self.details.evaluated_price, Decimal("9613518.63"))

    def test_a_placeholder_address_is_dropped_rather_than_stored(self):
        details = parse_award_text(
            "<p>Awarded Bidder(s):<br>ARTUR OBSLEDOVANIE (1113192)<br>-<br>"
            "Country: Uzbekistan</p>"
        )
        self.assertEqual(details.supplier_address, "")
        self.assertEqual(details.supplier_country, "Uzbekistan")


class ParseNoticeAwardTests(TestCase):
    def test_stores_a_structured_award(self):
        notice = TenderNotice.objects.create(
            notice_id="OP00402824",
            notice_type="Contract Award",
            country="Uzbekistan",
            notice_date=date(2025, 6, 25),
            notice_text_raw=AWARD_HTML,
        )

        award = parse_notice_award(notice)

        self.assertIsNotNone(award)
        self.assertEqual(award.supplier_name, "GLORY OFFICE SOLUTION")
        self.assertEqual(award.contract_price, Decimal("15952213.00"))
        self.assertEqual(ContractAward.objects.count(), 1)

    def test_non_award_notices_are_ignored(self):
        notice = TenderNotice.objects.create(
            notice_id="OP1", notice_type="Invitation for Bids",
            notice_text_raw=AWARD_HTML,
        )
        self.assertIsNone(parse_notice_award(notice))

    def test_reparsing_updates_rather_than_duplicates(self):
        notice = TenderNotice.objects.create(
            notice_id="OP2", notice_type="Contract Award", notice_text_raw=AWARD_HTML
        )
        parse_notice_award(notice)
        parse_notice_award(notice)
        self.assertEqual(ContractAward.objects.count(), 1)

    def test_parse_pending_skips_bodies_without_award_details(self):
        TenderNotice.objects.create(
            notice_id="OP3", notice_type="Contract Award", notice_text_raw=AWARD_HTML
        )
        TenderNotice.objects.create(
            notice_id="OP4", notice_type="Contract Award",
            notice_text_raw="<p>Contract Award</p><p>No details published.</p>",
        )

        result = parse_pending_awards()

        self.assertEqual(result["parsed"], 1)
        self.assertEqual(result["skipped"], 1)


class SupplierWebsiteTests(TestCase):
    def setUp(self):
        notice = TenderNotice.objects.create(
            notice_id="OP5", notice_type="Contract Award",
            bid_description="Supply of laboratory equipment",
            notice_text_raw=AWARD_HTML,
        )
        self.award = parse_notice_award(notice)

    def test_url_validation_rejects_directories_and_search_engines(self):
        self.assertFalse(enrichment.is_plausible_company_url("https://www.google.com/search?q=x"))
        self.assertFalse(enrichment.is_plausible_company_url("https://linkedin.com/company/x"))
        self.assertFalse(enrichment.is_plausible_company_url("ftp://example.com"))
        self.assertFalse(enrichment.is_plausible_company_url("not-a-url"))
        self.assertTrue(enrichment.is_plausible_company_url("https://gloryoffice.com.bd"))

    @patch("apps.tenders.services.ai.enrichment._responds", return_value=True)
    @patch("apps.tenders.services.ai.enrichment.search_answer",
           return_value="https://gloryoffice.com.bd")
    @patch("apps.tenders.services.ai.enrichment.search_enabled", return_value=True)
    def test_found_website_is_stored(self, _enabled, _search, _live):

        result = enrichment.enrich_award(self.award)

        self.assertTrue(result.found)
        self.award.refresh_from_db()
        self.assertEqual(self.award.supplier_website, "https://gloryoffice.com.bd")
        self.assertEqual(self.award.supplier_website_source, enrichment.SOURCE_AI_SEARCH)
        self.assertIsNotNone(self.award.supplier_website_checked_at)

    @patch("apps.tenders.services.ai.enrichment.search_answer", return_value="NONE")
    @patch("apps.tenders.services.ai.enrichment.search_enabled", return_value=True)
    def test_none_answer_records_the_check_without_a_url(self, _enabled, _search):

        enrichment.enrich_award(self.award)

        self.award.refresh_from_db()
        self.assertEqual(self.award.supplier_website, "")
        # The attempt is recorded so the same award is not retried forever.
        self.assertIsNotNone(self.award.supplier_website_checked_at)

    @patch("apps.tenders.services.ai.enrichment.search_answer",
           return_value="https://www.google.com/search?q=glory")
    @patch("apps.tenders.services.ai.enrichment.search_enabled", return_value=True)
    def test_junk_answer_is_rejected(self, _enabled, _search):

        enrichment.enrich_award(self.award)

        self.award.refresh_from_db()
        self.assertEqual(self.award.supplier_website, "")

    @patch("apps.tenders.services.ai.enrichment._responds", return_value=False)
    @patch("apps.tenders.services.ai.enrichment.search_answer",
           return_value="https://hwgrp.com")
    @patch("apps.tenders.services.ai.enrichment.search_enabled", return_value=True)
    def test_a_domain_that_answers_nothing_is_not_stored(self, _enabled, _search, _live):
        # Observed live: a plausible domain that passes every shape rule,
        # resolves in DNS, and then refuses every connection.
        enrichment.enrich_award(self.award)
        self.award.refresh_from_db()
        self.assertEqual(self.award.supplier_website, "")
        # Still recorded as checked, so it is not looked up again.
        self.assertIsNotNone(self.award.supplier_website_checked_at)

    @patch("apps.tenders.services.ai.enrichment.search_answer")
    @patch("apps.tenders.services.ai.enrichment.search_enabled", return_value=True)
    def test_a_field_label_is_never_searched_for(self, _enabled, search):
        # The parser carries "Name:" through on a couple of rows; searching
        # for it spends a live lookup to learn nothing.
        result = enrichment.find_company_website("Name:")
        self.assertFalse(result.checked)
        search.assert_not_called()

    @patch("apps.tenders.services.ai.enrichment.search_enabled", return_value=False)
    def test_no_provider_is_a_no_op(self, _enabled):
        result = enrichment.enrich_pending_awards()
        self.assertEqual(result, {"checked": 0, "found": 0, "skipped": 0})



# The second layout upstream publishes under `notice_type: Contract Award`.
# Every label that matters differs from the one above: the date, the awardee
# heading, and a price printed as three bare labels followed by two values.
SMALL_ASSIGNMENT_HTML = (
    "<div><h4><u>Small Assignment Contract Award</u></h4>"
    "<p><b>Project:</b>P146970-Third Village Investment Project<br>"
    "<b>Bid/Contract Reference No:</b>IDA-AFVIP3-CHV-IC-2022-47<br>"
    "<b>Procurement Method:</b>INDV-Individual Consultant Selection</p>"
    "<p>Contract Signature Date<br>(YYYY/MM/DD)<br>2022/10/19<br>"
    "Duration of Contract<br>71 Day(s)</p>"
    "<p>Awarded Firm/Individual:<br>"
    "HALDI CONSULT LLC (364477)<br>"
    "Country: Kyrgyz Republic<br>"
    "Registry ID: 23009196500120</p>"
    "<p>Price:<br>Currency:<br>Amount:<br>"
    "Kyrgyzstan Som (Kyrgyzstan Som)<br>202142.86</p></div>"
)


class SmallAssignmentTemplateTests(SimpleTestCase):
    """The layout that 2,989 mirrored award notices use and the parser did not.

    They were never malformed — they were a second template nobody had read,
    and every one of them was dropped for having no winner and no price.
    """

    def setUp(self):
        self.details = parse_award_text(SMALL_ASSIGNMENT_HTML)

    def test_the_signature_date_is_read_as_the_award_date(self):
        """This template has no "Date Notification of Award Issued"; the
        signature date is the only date it publishes for the contract."""
        self.assertEqual(self.details.award_date, date(2022, 10, 19))

    def test_the_awarded_firm_heading_opens_the_winner_section(self):
        self.assertEqual(self.details.supplier_name, "HALDI CONSULT LLC")
        self.assertEqual(self.details.supplier_reference, "364477")
        self.assertEqual(self.details.supplier_country, "Kyrgyz Republic")

    def test_the_registry_id_does_not_become_a_street_address(self):
        """It sits where an address line would, between the country and the
        next section."""
        self.assertNotIn("23009196500120", self.details.supplier_address)

    def test_the_stacked_price_block_is_read(self):
        self.assertEqual(self.details.contract_price, Decimal("202142.86"))

    def test_the_currency_name_is_stored_as_printed_without_the_echo(self):
        """Upstream repeats the name in brackets. Mapping these onto ISO codes
        would be a fact this codebase has no source for, so the name is kept
        and the front end renders it as `NAME 1 234,56`."""
        self.assertEqual(self.details.currency, "Kyrgyzstan Som")

    def test_the_duration_survives_the_other_labels(self):
        self.assertEqual(self.details.contract_duration, "71 Day(s)")


class AnonymousAwardeeTests(SimpleTestCase):
    """`Individual Consultant` is a placeholder, not a company."""

    def _details(self, awardee: str):
        return parse_award_text(
            SMALL_ASSIGNMENT_HTML.replace("HALDI CONSULT LLC (364477)", awardee)
        )

    def test_it_never_reaches_the_flat_winner_columns(self):
        """1,744 awards carry it. Promoting it would invent the largest firm
        in the archive and put it at the top of `companies.py`."""
        details = self._details("Individual Consultant")

        self.assertEqual(details.supplier_name, "")
        self.assertEqual(details.supplier_reference, "")

    def test_the_contract_is_still_worth_storing(self):
        """The price and the date are real even when the winner is not named,
        so the row belongs in the archive — it just names nobody."""
        details = self._details("Individual Consultant")

        self.assertTrue(details.is_useful)
        self.assertEqual(details.contract_price, Decimal("202142.86"))

    def test_a_named_firm_is_unaffected(self):
        details = self._details("REAL FIRM LLC (999111)")

        self.assertEqual(details.supplier_name, "REAL FIRM LLC")


class LabelCollisionTests(SimpleTestCase):
    """`price` is a label word and also the start of a real company's name."""

    def test_a_company_beginning_with_a_label_word_is_still_a_company(self):
        """PRICEWATERHOUSECOOPERS wins consulting contracts in this archive.
        Matching the bare labels as prefixes would have dropped it."""
        html = SMALL_ASSIGNMENT_HTML.replace(
            "HALDI CONSULT LLC (364477)", "PRICEWATERHOUSECOOPERS (123456)"
        )

        details = parse_award_text(html)

        self.assertEqual(details.supplier_name, "PRICEWATERHOUSECOOPERS")
