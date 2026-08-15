"""Sub-directions inside Consulting.

The rules were calibrated against the real corpus, and two mistakes it made
along the way are pinned here so they cannot come back:

* a bare `procurement` keyword matched every notice, because the Bank's own
  boilerplate contains the word — it put 32% of consulting into "legal";
* single generic words were being matched against the multi-page body rather
  than the title, which is what made that possible.
"""

from __future__ import annotations

from django.test import SimpleTestCase

from apps.tenders.categories import TenderCategory
from apps.tenders.subcategories import ConsultingSubcategory, classify_sub


def sub(description: str, *, notice_text: str = "", project_name: str = "") -> str:
    return classify_sub(
        category=TenderCategory.CONSULTING,
        description=description,
        project_name=project_name,
        notice_text=notice_text,
    ).subcategory


class ScopeTests(SimpleTestCase):
    def test_only_consulting_is_split(self):
        """The other five directions are already actionable on their own."""
        for category in [
            TenderCategory.CONSTRUCTION,
            TenderCategory.SUPPLY,
            TenderCategory.SERVICES,
            TenderCategory.IT,
            TenderCategory.OTHER,
        ]:
            with self.subTest(category=category):
                guess = classify_sub(
                    category=category, description="Audit of financial statements"
                )
                self.assertEqual(guess.subcategory, ConsultingSubcategory.UNKNOWN)


class EnglishTests(SimpleTestCase):
    def test_recognises_each_sub_direction(self):
        cases = {
            "Consulting services for construction supervision of the road":
                ConsultingSubcategory.ENGINEERING,
            "External audit of the financial statements for FY2025":
                ConsultingSubcategory.AUDIT,
            "Environmental and Social Impact Assessment for the dam":
                ConsultingSubcategory.ENVIRONMENT_SOCIAL,
            "Capacity building and training of trainers programme":
                ConsultingSubcategory.TRAINING,
            "Baseline survey and impact evaluation of the programme":
                ConsultingSubcategory.RESEARCH,
            "Development of a Management Information System":
                ConsultingSubcategory.IT_ADVISORY,
            "Procurement Specialist":
                ConsultingSubcategory.LEGAL_PROCUREMENT,
            "Project Implementation Unit management support":
                ConsultingSubcategory.MANAGEMENT,
        }
        for description, expected in cases.items():
            with self.subTest(description=description):
                self.assertEqual(sub(description), expected)


class MultilingualTests(SimpleTestCase):
    """Notices arrive in English, French, Spanish and Portuguese."""

    def test_french(self):
        self.assertEqual(
            sub("Surveillance et Contrôle des travaux de réhabilitation"),
            ConsultingSubcategory.ENGINEERING,
        )
        self.assertEqual(
            sub("Recrutement d'un cabinet pour l'audit financier du projet"),
            ConsultingSubcategory.AUDIT,
        )
        self.assertEqual(
            sub("Renforcement des capacités des agents"),
            ConsultingSubcategory.TRAINING,
        )

    def test_accents_and_ligatures_are_folded(self):
        """`œuvre` must not break into a token the rules cannot see."""
        self.assertEqual(
            sub("Appui à la mise en œuvre du projet"),
            ConsultingSubcategory.MANAGEMENT,
        )

    def test_spanish(self):
        self.assertEqual(
            sub("Auditoría externa de los estados financieros"),
            ConsultingSubcategory.AUDIT,
        )
        self.assertEqual(
            sub("Especialista en adquisiciones para la unidad"),
            ConsultingSubcategory.LEGAL_PROCUREMENT,
        )

    def test_portuguese(self):
        self.assertEqual(
            sub("Serviços de consultoria especializada em engenharia"),
            ConsultingSubcategory.ENGINEERING,
        )


class BoilerplateTests(SimpleTestCase):
    """The regression that made sub-classification useless on the first pass."""

    #: Every World Bank notice ends with wording like this.
    BOILERPLATE = (
        "The consultant will be selected in accordance with the procurement "
        "procedures set out in the World Bank Procurement Regulations for IPF "
        "Borrowers. Interested parties must comply with the legal requirements "
        "of the Borrower and submit their expression of interest."
    )

    def test_procurement_boilerplate_does_not_decide_the_sub_direction(self):
        """A word in the body must not outvote the subject in the title."""
        result = sub(
            "Recrutement d'un topographe pour les travaux",
            notice_text=self.BOILERPLATE,
        )
        self.assertEqual(result, ConsultingSubcategory.ENGINEERING)
        self.assertNotEqual(result, ConsultingSubcategory.LEGAL_PROCUREMENT)

    def test_boilerplate_alone_yields_other_not_a_confident_guess(self):
        """With nothing in the title, "other" is the honest answer."""
        self.assertEqual(
            sub("Consulting services", notice_text=self.BOILERPLATE),
            ConsultingSubcategory.OTHER,
        )

    def test_multi_word_phrases_are_still_read_from_the_body(self):
        """Only *single* generic words are restricted to the title."""
        self.assertEqual(
            sub(
                "Recruitment of a firm",
                notice_text="The assignment covers construction supervision of "
                "the new bypass, including a bill of quantities.",
            ),
            ConsultingSubcategory.ENGINEERING,
        )


class ConfidenceTests(SimpleTestCase):
    def test_weak_winner_falls_back_to_other(self):
        guess = classify_sub(
            category=TenderCategory.CONSULTING,
            description="Consulting services for the project",
        )
        self.assertEqual(guess.subcategory, ConsultingSubcategory.OTHER)

    def test_a_clear_title_scores_high(self):
        guess = classify_sub(
            category=TenderCategory.CONSULTING,
            description="External audit of the financial statements, audit report",
        )
        self.assertEqual(guess.subcategory, ConsultingSubcategory.AUDIT)
        self.assertGreater(guess.confidence, 0.5)

    def test_rationale_names_the_runner_up(self):
        guess = classify_sub(
            category=TenderCategory.CONSULTING,
            description="Training and capacity building on environmental safeguards",
        )
        self.assertIn("score=", guess.rationale)
