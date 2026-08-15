"""Reading a notice's three contact tiers.

The address blocks here are copied from live notices, because every hard case
in this module came from one. The four that shaped the implementation:

* **OP00459066 (Azerbaijan)** — the block runs into the street address with no
  punctuation ("Project Coordinator 27, Nazim Hikmet str."), which swallowed
  the job title and, with it, the whole match.
* **OP00456662 (Uzbekistan)** — three addresses under one "Attn:" line, of
  which one is the named person's, one is his unit's, and one belongs to a
  colleague who is never named.
* **OP00459323 (Tajikistan)** — the structured field and the body disagree, and
  the body holds the address bids are actually delivered to.
* **Transliteration** — "Mirodil Khusanov" signs with ``m.xusanov@``; the same
  human must not appear as two people.
"""

from __future__ import annotations

from django.test import SimpleTestCase

from apps.tenders.contacts import (
    PURPOSE_ENQUIRY,
    PURPOSE_SUBMISSION,
    SOURCE_PROJECT_ESRS,
    SOURCE_PROJECT_FEED,
    TIER_BANK,
    TIER_BODY,
    TIER_NOTICE,
    Contact,
    build_contacts,
    extract_body_contacts,
    same_person,
)
from apps.tenders.esrs import EsrsContact

AZERBAIJAN_BLOCK = (
    "Further information can be obtained at the address below during office "
    "hours (Monday to Friday 09:00 to 17:00 hours). Expressions of interest "
    "must be delivered in a written form to the address below (in person, or "
    "by mail, or by fax, or by e-mail) by August 18, 2026. Ministry of Labor "
    "and Social Protection of Population / Employment Support Project "
    "Attn: Mr. Fagan Asgarov, Project Coordinator 27, Nazim Hikmet str., Rich "
    "Plaza, 1st entrance, 2nd floor AZ1100, Baku, Azerbaijan "
    "Tel: (99412) 5253449 E-mail: fagan.asgarov@esp.az; aybeniz.hajiyeva@esp.az"
)

UZBEKISTAN_BLOCK = (
    "Expressions of interest, demonstrating relevant qualifications and "
    "experience must be delivered electronically to the email address "
    "piuinsonprocurement@gmail.com by August 7, 2026. PIU INSON under the NASP "
    "Attn: Mr. Mirodil Khusanov - Project Coordinator Address: 3, Amir Temur "
    "str., Tashkent, Uzbekistan, 100060 Phone number: +998712395976 "
    "E-mail: piuinsonprocurement@gmail.com : m.xusanov@ihma.uz; s.maxmudov@ihma.uz"
)

TAJIKISTAN_BLOCK = (
    "The address referred to above is: For the purpose of inquiries, receipt "
    "and submission of tender documents: To: Satori D.A. - PMU Director "
    "Address: Bokhtar street 17, 12th Floor City: Dushanbe Country: Tajikistan "
    "E-mail: procurement.srasp@gmail.com"
)


class FakeNotice:
    """The seven contact columns, which is all `build_contacts` reads."""

    def __init__(self, **kwargs):
        defaults = {
            "contact_name": "", "contact_organization": "", "contact_email": "",
            "contact_phone_no": "", "contact_address": "", "contact_country": "",
            "contact_web_url": "",
        }
        for key, value in {**defaults, **kwargs}.items():
            setattr(self, key, value)


class FakeProfile:
    def __init__(self, team_lead=""):
        self.team_lead = team_lead


class BodyExtractionTests(SimpleTestCase):
    def test_reads_a_name_that_runs_into_the_street_address(self):
        found = extract_body_contacts(AZERBAIJAN_BLOCK)
        named = [c for c in found if c.name]
        self.assertEqual(len(named), 1)
        self.assertEqual(named[0].name, "Fagan Asgarov")
        # The title stops at the house number and does not swallow it.
        self.assertEqual(named[0].role, "Project Coordinator")
        self.assertEqual(named[0].email, "fagan.asgarov@esp.az")

    def test_a_bracketed_country_code_is_still_a_phone_number(self):
        found = extract_body_contacts(AZERBAIJAN_BLOCK)
        self.assertEqual(found[0].phone, "(99412) 5253449")

    def test_a_colleagues_address_is_not_filed_under_the_named_person(self):
        found = extract_body_contacts(AZERBAIJAN_BLOCK)
        asgarov = next(c for c in found if c.name)
        self.assertNotIn("aybeniz.hajiyeva@esp.az", asgarov.alternate_emails)
        # Kept, but nameless: the notice never said whose it is.
        loose = [c for c in found if not c.name]
        self.assertEqual([c.email for c in loose], ["aybeniz.hajiyeva@esp.az"])

    def test_an_address_is_filed_under_the_person_its_local_part_names(self):
        found = extract_body_contacts(UZBEKISTAN_BLOCK)
        khusanov = next(c for c in found if c.name)
        self.assertEqual(khusanov.email, "m.xusanov@ihma.uz")
        # The colleague listed in the same run is a different human and must
        # not inherit the name above him.
        self.assertNotIn("s.maxmudov@ihma.uz", khusanov.alternate_emails)
        self.assertIn("s.maxmudov@ihma.uz", [c.email for c in found if not c.name])

    def test_an_address_is_read_where_the_notice_explains_it(self):
        # The unit mailbox appears twice: once in the sentence that says what
        # it is for, and again bare in the sign-off block. The first wins, so
        # the address keeps its purpose instead of arriving unlabelled under a
        # name that merely sits above it.
        found = extract_body_contacts(UZBEKISTAN_BLOCK)
        unit = next(c for c in found if c.email == "piuinsonprocurement@gmail.com")
        self.assertEqual(unit.purpose, PURPOSE_SUBMISSION)

    def test_delivery_wording_outranks_the_inquiry_wording_beside_it(self):
        # "For the purpose of inquiries, receipt and submission of tender
        # documents" names both jobs; getting the delivery address wrong is the
        # one that costs a bid.
        found = extract_body_contacts(TAJIKISTAN_BLOCK)
        self.assertEqual(found[0].purpose, PURPOSE_SUBMISSION)

    def test_an_address_offered_only_for_questions_says_so(self):
        found = extract_body_contacts(
            "Further information can be obtained at info@agency.gov.kz during "
            "office hours."
        )
        self.assertEqual(found[0].purpose, PURPOSE_ENQUIRY)

    def test_an_empty_body_yields_nothing(self):
        self.assertEqual(extract_body_contacts(""), [])

    def test_a_block_with_a_name_and_only_a_phone_still_reaches_someone(self):
        found = extract_body_contacts("Attn: Mr. John Smith Tel: +998 71 200 30 40")
        self.assertEqual(found[0].name, "John Smith")
        self.assertEqual(found[0].email, "")
        self.assertTrue(found[0].is_reachable)


class NameMatchingTests(SimpleTestCase):
    def test_transliteration_variants_are_one_person(self):
        self.assertTrue(same_person("Mirodil Khusanov", "M. Xusanov"))

    def test_different_people_stay_different(self):
        self.assertFalse(same_person("Mirodil Khusanov", "S. Maxmudov"))

    def test_initials_alone_never_match(self):
        # "D.A." against "D. S." would otherwise merge two strangers.
        self.assertFalse(same_person("D.A.", "D.S."))


class TierAssemblyTests(SimpleTestCase):
    def test_the_three_tiers_come_back_in_priority_order(self):
        notice = FakeNotice(
            contact_name="Mirodil Khusanov",
            contact_email="piuinsonprocurement@gmail.com",
            contact_phone_no="+998712395976",
        )
        result = build_contacts(
            notice,
            FakeProfile("Marina Novikova,Vlad Alexandru Grigoras"),
            body_text=UZBEKISTAN_BLOCK,
        )
        self.assertEqual(
            [group["tier"] for group in result["groups"]],
            [TIER_NOTICE, TIER_BODY, TIER_BANK],
        )
        self.assertEqual([g["priority"] for g in result["groups"]], [1, 2, 3])

    def test_an_address_already_published_as_a_field_is_not_repeated(self):
        notice = FakeNotice(
            contact_name="Mirodil Khusanov",
            contact_email="piuinsonprocurement@gmail.com",
        )
        result = build_contacts(notice, None, body_text=UZBEKISTAN_BLOCK)
        body = next(g for g in result["groups"] if g["tier"] == TIER_BODY)
        every_address = {
            address
            for contact in body["contacts"]
            for address in (contact.email, *contact.alternate_emails)
        }
        self.assertNotIn("piuinsonprocurement@gmail.com", every_address)

    def test_the_same_person_at_a_second_address_is_flagged_not_dropped(self):
        notice = FakeNotice(
            contact_name="Mirodil Khusanov",
            contact_email="piuinsonprocurement@gmail.com",
        )
        result = build_contacts(notice, None, body_text=UZBEKISTAN_BLOCK)
        body = next(g for g in result["groups"] if g["tier"] == TIER_BODY)
        khusanov = next(c for c in body["contacts"] if c.name)
        self.assertEqual(khusanov.email, "m.xusanov@ihma.uz")
        self.assertTrue(khusanov.same_as_primary)

    def test_the_body_keeps_a_delivery_address_the_field_does_not_have(self):
        # The regression this tier exists for: the field holds the unit's
        # general address, the body holds the one bids go to.
        notice = FakeNotice(contact_name="Daler Satori", contact_email="aedpmu@gmail.com")
        result = build_contacts(notice, None, body_text=TAJIKISTAN_BLOCK)
        body = next(g for g in result["groups"] if g["tier"] == TIER_BODY)
        self.assertEqual(body["contacts"][0].email, "procurement.srasp@gmail.com")
        self.assertEqual(body["contacts"][0].purpose, PURPOSE_SUBMISSION)

    def test_a_phone_repeated_from_the_field_is_not_a_second_way_to_call(self):
        notice = FakeNotice(
            contact_name="Mirodil Khusanov",
            contact_email="piuinsonprocurement@gmail.com",
            # Same number, written differently.
            contact_phone_no="+998 71 239 59 76",
        )
        result = build_contacts(notice, None, body_text=UZBEKISTAN_BLOCK)
        body = next(g for g in result["groups"] if g["tier"] == TIER_BODY)
        self.assertEqual([c.phone for c in body["contacts"]], ["", ""])

    def test_an_award_notice_with_no_contact_fields_has_no_first_tier(self):
        result = build_contacts(FakeNotice(), None, body_text="")
        self.assertEqual(result["groups"], [])
        self.assertEqual(result["total"], 0)

    def test_team_leads_are_split_and_carry_no_invented_address(self):
        result = build_contacts(
            FakeNotice(),
            FakeProfile("Koji Nishida,Irina Voitekhovitch,Jianping Zhao"),
            body_text="",
        )
        bank = next(g for g in result["groups"] if g["tier"] == TIER_BANK)
        self.assertEqual(
            [c.name for c in bank["contacts"]],
            ["Koji Nishida", "Irina Voitekhovitch", "Jianping Zhao"],
        )
        self.assertEqual({c.email for c in bank["contacts"]}, {""})

    def test_stored_enrichment_is_attached_to_the_matching_team_lead(self):
        result = build_contacts(
            FakeNotice(),
            FakeProfile("Mohini Kak"),
            body_text="",
            enrichment={
                "Mohini Kak": {
                    "title": "Senior Health Specialist",
                    "unit": "Health, Nutrition & Population",
                    "work_email": "mkak@worldbank.org",
                    "email_confirmed": False,
                }
            },
        )
        lead = result["groups"][0]["contacts"][0]
        self.assertEqual(lead.role, "Senior Health Specialist")
        self.assertEqual(lead.email, "mkak@worldbank.org")
        # Derived, so the UI must be told not to present it as confirmed.
        self.assertFalse(lead.email_confirmed)


class EsrsTierTests(SimpleTestCase):
    """The ESRS turns tier 3 from a list of names into people a vendor can write to."""

    LEADS = "Marina Novikova,Solene Marie Paule Rougeaux,Vlad Alexandru Grigoras"

    def _esrs(self, *rows) -> list[EsrsContact]:
        return [
            EsrsContact(name=name, title=title, email=email, label=label)
            for name, title, email, label in rows
        ]

    def _bank(self, **kwargs):
        result = build_contacts(FakeNotice(), FakeProfile(self.LEADS), body_text="", **kwargs)
        return next(g for g in result["groups"] if g["tier"] == TIER_BANK)["contacts"]

    def test_a_published_address_reaches_the_team_lead_the_feed_only_named(self):
        contacts = self._bank(
            esrs_contacts=self._esrs(
                (
                    "Marina Novikova",
                    "Senior Social Protection Economist",
                    "mnovikova@worldbank.org",
                    "Task Team Leader",
                )
            )
        )

        self.assertEqual(contacts[0].email, "mnovikova@worldbank.org")
        self.assertEqual(contacts[0].role, "Senior Social Protection Economist")
        # Published by the World Bank, not derived from a staff address pattern.
        self.assertTrue(contacts[0].email_confirmed)
        self.assertEqual(contacts[0].source, SOURCE_PROJECT_ESRS)

    def test_a_lead_the_esrs_does_not_name_keeps_the_older_shape(self):
        """One document short of complete must not empty the tier."""
        contacts = self._bank(
            esrs_contacts=self._esrs(
                ("Marina Novikova", "Senior Economist", "mnovikova@worldbank.org", "Task Team Leader")
            )
        )

        self.assertEqual(contacts[1].name, "Solene Marie Paule Rougeaux")
        self.assertEqual(contacts[1].email, "")
        self.assertEqual(contacts[1].source, SOURCE_PROJECT_FEED)

    def test_the_feed_decides_the_order_of_the_tier(self):
        """It is the source that says who is accountable; the ESRS only fills rows in."""
        contacts = self._bank(
            esrs_contacts=self._esrs(
                ("Vlad Alexandru Grigoras", "Senior Specialist", "v@worldbank.org", "TTL Contact"),
                ("Marina Novikova", "Senior Economist", "m@worldbank.org", "Task Team Leader"),
            )
        )

        self.assertEqual(
            [c.name for c in contacts],
            ["Marina Novikova", "Solene Marie Paule Rougeaux", "Vlad Alexandru Grigoras"],
        )

    def test_a_published_title_outranks_a_searched_one(self):
        """One was printed in a Bank document; the other was found on the web."""
        contacts = self._bank(
            enrichment={
                "Marina Novikova": {
                    "title": "Economist",
                    "work_email": "guess@worldbank.org",
                    "country_office": "Tashkent",
                }
            },
            esrs_contacts=self._esrs(
                (
                    "Marina Novikova",
                    "Senior Social Protection Economist",
                    "mnovikova@worldbank.org",
                    "Task Team Leader",
                )
            ),
        )

        self.assertEqual(contacts[0].role, "Senior Social Protection Economist")
        self.assertEqual(contacts[0].email, "mnovikova@worldbank.org")
        # What only the enrichment carries is still kept.
        self.assertEqual(contacts[0].country, "Tashkent")

    def test_someone_the_esrs_names_and_the_feed_does_not_is_added_last(self):
        """Two publications of different ages; the newer one may know one more name."""
        contacts = self._bank(
            esrs_contacts=self._esrs(
                ("Aki Tsuda", "Senior Social Specialist", "atsuda@worldbank.org", "TTL Contact")
            )
        )

        self.assertEqual(len(contacts), 4)
        self.assertEqual(contacts[-1].name, "Aki Tsuda")
        self.assertEqual(contacts[-1].email, "atsuda@worldbank.org")

    def test_a_transliterated_name_is_still_the_same_person(self):
        """The same fold that stops one human appearing twice in tiers 1 and 2."""
        result = build_contacts(
            FakeNotice(),
            FakeProfile("Mirodil Khusanov"),
            body_text="",
            esrs_contacts=self._esrs(
                ("Mirodil Xusanov", "Senior Specialist", "mx@worldbank.org", "TTL Contact")
            ),
        )
        contacts = next(g for g in result["groups"] if g["tier"] == TIER_BANK)["contacts"]

        self.assertEqual(len(contacts), 1)
        self.assertEqual(contacts[0].name, "Mirodil Khusanov")
        self.assertEqual(contacts[0].email, "mx@worldbank.org")

    def test_a_project_with_no_esrs_behaves_exactly_as_before(self):
        contacts = self._bank()

        self.assertEqual(len(contacts), 3)
        self.assertEqual({c.email for c in contacts}, {""})
        self.assertEqual({c.source for c in contacts}, {SOURCE_PROJECT_FEED})

    def test_a_project_the_feed_names_nobody_for_still_gets_a_tier(self):
        """P509487 on the mirror: `teamleadname` is empty and the ESRS names two."""
        result = build_contacts(
            FakeNotice(),
            FakeProfile(""),
            body_text="",
            esrs_contacts=self._esrs(
                ("Mansur Bustoni", "Senior Transport Specialist", "mbustoni@worldbank.org", "Task Team Leader"),
                ("Saroj Ayush", "Senior Transport Specialist", "sayush@worldbank.org", "TTL Contact"),
            ),
        )
        contacts = next(g for g in result["groups"] if g["tier"] == TIER_BANK)["contacts"]

        self.assertEqual([c.name for c in contacts], ["Mansur Bustoni", "Saroj Ayush"])
        self.assertTrue(all(c.is_reachable for c in contacts))

    def test_the_two_publications_are_merged_rather_than_one_replacing_the_other(self):
        """P177895 on the mirror: the lists overlap on exactly one of four people."""
        result = build_contacts(
            FakeNotice(),
            FakeProfile("Blerta Qerimi,Syed Mehdi Hassan"),
            body_text="",
            esrs_contacts=self._esrs(
                ("Graciela Miralles Murciego", "Senior Economist", "gmiralles@worldbank.org", "Task Team Leader"),
                ("Zhihua Zeng", "Senior Economist", "Zzeng@worldbank.org", "TTL Contact"),
                ("Syed Mehdi Hassan", "Senior Financial Sector Specialist", "shassan6@worldbank.org", "TTL Contact"),
            ),
        )
        contacts = next(g for g in result["groups"] if g["tier"] == TIER_BANK)["contacts"]

        self.assertEqual(
            [(c.name, bool(c.email)) for c in contacts],
            [
                ("Blerta Qerimi", False),
                ("Syed Mehdi Hassan", True),
                ("Graciela Miralles Murciego", True),
                ("Zhihua Zeng", True),
            ],
            "the feed leads, and neither list may swallow the other",
        )


class ContactShapeTests(SimpleTestCase):
    def test_priority_follows_the_tier(self):
        self.assertEqual(Contact(tier=TIER_NOTICE, source="x").priority, 1)
        self.assertEqual(Contact(tier=TIER_BODY, source="x").priority, 2)
        self.assertEqual(Contact(tier=TIER_BANK, source="x").priority, 3)

    def test_a_name_without_an_address_is_not_reachable(self):
        self.assertFalse(Contact(tier=TIER_BANK, source="x", name="A B").is_reachable)
