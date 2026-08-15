"""What the ESRS publishes about the Bank's own staff, and what it does not.

The sample below is the real shape of a mirrored file — pypdf's output for
P177895, page furniture and all. Nothing here is reformatted to make the parse
easier: the header lines between the heading and the first name, the trailing
whitespace after each address, and the appraisal section that follows are the
things this module has to survive.
"""

from __future__ import annotations

from django.test import SimpleTestCase, TestCase

from apps.tenders import esrs
from apps.tenders.models import HarvestedDocument, ProjectProfile

#: One mirrored ESRS, from "CONTACT POINT" to the end of the document.
SAMPLE = """\
III. CONTACT POINT
World Bank

The World Bank
Msme Competitiveness Project (P177895)
Oct 08, 2025 Page 16 of 16
For Official Use Only
Public Disclosure
Task Team Leader: Graciela Miralles Murciego Title: Senior Economist
Email: gmiralles@worldbank.org
TTL Contact: Zhihua Zeng Job Title: Senior Economist
Email: Zzeng@worldbank.org
TTL Contact: Syed Mehdi Hassan Job Title: Senior Financial Sector Specialist
Email: shassan6@worldbank.org
IV. FOR MORE INFORMATION CONTACT
The World Bank
1818 H Street, NW
Washington, D.C. 20433
Telephone: (202) 473-1000
Web: http://www.worldbank.org/projects
V. APPROVAL
Task Team Leader(s): Graciela Miralles Murciego, Zhihua Zeng, Syed Mehdi Hassan
ADM Environmental Specialist: Adrian Laurentiu Mihailescu
ADM Social Specialist: Aki Tsuda
"""


#: The older template, from the mirrored P171250. Ten of the nineteen ESRS
#: files on the server are this shape: the heading is plural, the section is
#: numbered V, every person is labelled "Contact", the address shares a line
#: with a phone number, and the block runs on into the borrower's side.
OLDER_TEMPLATE = """\
V. CONTACT POINTS
World Bank
Contact: Maddalena Honorati Title: Senior Economist
Telephone No: 1-202-468103 Email: mhonorati@worldbank.org
Contact: Sandor I. Karacsony Title: Senior Economist
Telephone No: 5220+383 / 4 Email: skaracsony@worldbank.org
Borrower/Client/Recipient
Borrower: Republic of Azerbaijan
Implementing Agency(ies)
Implementing Agency: Ministry of Labor and Social Protection of the Population
V. FOR MORE INFORMATION CONTACT
The World Bank
1818 H Street, NW
VI. APPROVAL
Task Team Leader(s): Maddalena Honorati, Sandor I. Karacsony
"""


class OlderTemplateTests(SimpleTestCase):
    """The shape more than half the mirrored files are actually in."""

    def setUp(self) -> None:
        self.contacts = esrs.extract_contacts(OLDER_TEMPLATE)

    def test_the_people_are_read_with_title_address_and_phone(self):
        self.assertEqual(
            [(c.name, c.title, c.email, c.phone) for c in self.contacts],
            [
                (
                    "Maddalena Honorati",
                    "Senior Economist",
                    "mhonorati@worldbank.org",
                    "1-202-468103",
                ),
                (
                    "Sandor I. Karacsony",
                    "Senior Economist",
                    "skaracsony@worldbank.org",
                    "5220+383 / 4",
                ),
            ],
        )

    def test_the_borrower_side_is_not_read_as_bank_contacts(self):
        """`Contact:` is only safe as a label because the block stops before this."""
        names = [c.name for c in self.contacts]
        self.assertNotIn("Republic of Azerbaijan", names)
        self.assertNotIn(
            "Ministry of Labor and Social Protection of the Population", names
        )
        self.assertEqual(len(self.contacts), 2)

    def test_no_lead_is_inferred_where_the_template_designates_none(self):
        """It labels everybody `Contact`; picking one would be our claim, not its."""
        self.assertFalse(any(c.is_lead for c in self.contacts))

    def test_the_current_template_publishes_no_phone_and_that_is_not_a_failure(self):
        self.assertEqual({c.phone for c in esrs.extract_contacts(SAMPLE)}, {""})


class BlockTests(SimpleTestCase):
    def test_the_block_ends_at_the_next_roman_heading(self):
        """Past it are the Bank's switchboard and a list of names with no addresses."""
        block = esrs.contact_block(SAMPLE)

        self.assertIn("Graciela Miralles Murciego", block)
        self.assertNotIn("1818 H Street", block)
        self.assertNotIn("Aki Tsuda", block)

    def test_a_document_with_no_contact_section_yields_nothing(self):
        """Most of the mirror is procurement plans, and this runs over all of it."""
        self.assertEqual(esrs.contact_block("A procurement plan, in full."), "")
        self.assertEqual(esrs.extract_contacts(""), [])


class ContactTests(SimpleTestCase):
    def setUp(self) -> None:
        self.contacts = esrs.extract_contacts(SAMPLE)

    def test_every_person_in_section_three_is_read_with_title_and_address(self):
        self.assertEqual(
            [(c.name, c.title, c.email) for c in self.contacts],
            [
                (
                    "Graciela Miralles Murciego",
                    "Senior Economist",
                    "gmiralles@worldbank.org",
                ),
                ("Zhihua Zeng", "Senior Economist", "Zzeng@worldbank.org"),
                (
                    "Syed Mehdi Hassan",
                    "Senior Financial Sector Specialist",
                    "shassan6@worldbank.org",
                ),
            ],
        )

    def test_the_templates_own_label_says_who_the_lead_is(self):
        """Deciding that ourselves would be a claim about how the Bank divides work."""
        self.assertTrue(self.contacts[0].is_lead)
        self.assertFalse(any(c.is_lead for c in self.contacts[1:]))

    def test_page_furniture_between_the_heading_and_the_names_is_ignored(self):
        """A line is a labelled contact line or it is nothing — no list of noise."""
        self.assertNotIn("Public Disclosure", [c.name for c in self.contacts])
        self.assertEqual(len(self.contacts), 3)

    def test_the_approval_section_is_not_read_as_contacts(self):
        """It names two more staff and gives no way to reach either of them."""
        self.assertNotIn("Aki Tsuda", [c.name for c in self.contacts])

    def test_a_person_whose_address_the_layout_lost_is_still_returned(self):
        """A name with a title beats the feed's bare name; a guessed address does not."""
        text = (
            "CONTACT POINT\nWorld Bank\n"
            "Task Team Leader: Marina Novikova Title: Senior Social Protection Economist\n"
            "For Official Use Only\nFor Official Use Only\nFor Official Use Only\n"
            "Email: mnovikova@worldbank.org\n"
        )
        found = esrs.extract_contacts(text)

        self.assertEqual(found[0].name, "Marina Novikova")
        self.assertEqual(found[0].title, "Senior Social Protection Economist")
        self.assertEqual(found[0].email, "")

    def test_an_address_is_never_claimed_across_a_second_name(self):
        """Publishing one person's address under another's name is the worst outcome."""
        text = (
            "CONTACT POINT\nWorld Bank\n"
            "Task Team Leader: Marina Novikova Title: Senior Economist\n"
            "TTL Contact: Vlad Alexandru Grigoras Job Title: Senior Specialist\n"
            "Email: vgrigoras@worldbank.org\n"
        )
        found = esrs.extract_contacts(text)

        self.assertEqual(found[0].email, "")
        self.assertEqual(found[1].email, "vgrigoras@worldbank.org")

    def test_a_name_repeated_under_two_labels_is_one_person(self):
        text = (
            "CONTACT POINT\nWorld Bank\n"
            "Task Team Leader: Mansur Bustoni Title: Senior Transport Specialist\n"
            "Email: mbustoni@worldbank.org\n"
            "TTL Contact: Mansur Bustoni Job Title: Senior Transport Specialist\n"
            "Email: mbustoni@worldbank.org\n"
        )
        self.assertEqual(len(esrs.extract_contacts(text)), 1)

    def test_a_row_with_no_title_still_carries_the_person(self):
        text = (
            "CONTACT POINT\nWorld Bank\n"
            "Task Team Leader: Saroj Ayush\n"
            "Email: sayush@worldbank.org\n"
        )
        found = esrs.extract_contacts(text)

        self.assertEqual(found[0].name, "Saroj Ayush")
        self.assertEqual(found[0].title, "")
        self.assertEqual(found[0].email, "sayush@worldbank.org")


class MirrorLookupTests(TestCase):
    """Reading the block off the mirror, keyed on the URL the feed published."""

    def setUp(self) -> None:
        self.profile = ProjectProfile.objects.create(
            project_id="P177895",
            esrs_pdf_url="https://documents.worldbank.org/curated/en/099/esrs.pdf",
        )

    def _mirror(self, *, status=HarvestedDocument.Status.FETCHED, text=SAMPLE):
        from apps.tenders.services.harvest import url_key

        return HarvestedDocument.objects.create(
            url_hash=url_key(self.profile.esrs_pdf_url),
            url=self.profile.esrs_pdf_url,
            kind=HarvestedDocument.Kind.PROJECT_DOC,
            status=status,
            text=text,
            text_chars=len(text),
            has_text_layer=True,
        )

    def test_the_mirrored_esrs_is_found_by_the_url_the_feed_published(self):
        self._mirror()

        self.assertEqual(len(esrs.contacts_for(self.profile)), 3)

    def test_a_project_whose_esrs_is_not_mirrored_yet_degrades_to_nothing(self):
        """The ordinary state: the harvester registers it and fetches on its own clock."""
        self.assertEqual(esrs.contacts_for(self.profile), [])

    def test_a_document_that_never_fetched_is_not_read(self):
        self._mirror(status=HarvestedDocument.Status.ACCESS_DENIED)

        self.assertEqual(esrs.contacts_for(self.profile), [])

    def test_a_project_with_no_esrs_url_costs_no_query(self):
        self.assertEqual(esrs.contacts_for(ProjectProfile(project_id="P1")), [])
