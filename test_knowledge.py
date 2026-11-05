import os
import sys
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import knowledge_cards as cards
import lunch_core as core

NTR = "탄수화물(g) : 90.0<br/>단백질(g) : 34.5<br/>칼슘(mg) : 173.4<br/>철분(mg) : 0.0"
# 나머지 3으로 단계 순서가 정해지므로, 영양 정보 우선인 날과 아닌 날을 나눈다.
MENU_FIRST_DAY = date(2026, 11, 4)
NUTRITION_FIRST_DAY = date(2026, 11, 3)


def meal(*names, ntr="", cal=""):
    return {"DDISH_NM": "<br/>".join(names), "NTR_INFO": ntr, "CAL_INFO": cal}


class Setup(unittest.TestCase):
    def test_days(self):
        self.assertNotEqual(MENU_FIRST_DAY.toordinal() % 3, 0)
        self.assertEqual(NUTRITION_FIRST_DAY.toordinal() % 3, 0)


class Matching(unittest.TestCase):
    def tiers(self, *names):
        return core._knowledge_tiers(meal(*names))

    def test_menu_card(self):
        self.assertIn("미역 같은 해조류에는 식이섬유가 풍부해요.", self.tiers("#황태채미역국 (5.6.13)")[0])

    def test_samgyeopsal(self):
        self.assertTrue(any("9kcal" in text for text in self.tiers("#삼겹살구이 (10)")[0]))

    def test_allergy_numbers_are_ignored(self):
        self.assertEqual(core.menu_names(meal("#쌈장 (5.6.13)")), ["쌈장"])

    def test_exclude(self):
        self.assertEqual(self.tiers("돈까스소스")[0], [])
        self.assertEqual(self.tiers("그린샐러드&오리엔탈D")[0], [])
        self.assertFalse(any("잡곡" in text for text in self.tiers("강원도욱수수찐빵")[1]))

    def test_wrong_links_found_in_real_menus(self):
        self.assertFalse(any("티아민" in text for text in self.tiers("두부등심탕수육&후르츠소스")[0]))
        self.assertFalse(any("비타민 C" in text for text in self.tiers("키위파인팝(아이스크림)")[0]))
        self.assertFalse(any("수증기" in text for text in self.tiers("매콤갈비찜")[1]))
        self.assertEqual(self.tiers("현미누룽지칩")[1], [])

    def test_exclude_applies_per_menu(self):
        self.assertTrue(self.tiers("돈까스소스", "제육볶음")[0])

    def test_soup_ending_does_not_match_inside_word(self):
        self.assertEqual(self.tiers("두부등심탕수육&후르츠소스")[1], [])
        self.assertTrue(any("국물" in text for text in self.tiers("우거지해장국")[1]))
        self.assertFalse(any("국물" in text for text in self.tiers("제주고기국수")[1]))

    def test_kimchi_category(self):
        self.assertTrue(self.tiers("배추김치")[1])
        self.assertEqual(self.tiers("배추김치")[0], [])


class Nutrition(unittest.TestCase):
    def test_parse(self):
        values = core.parse_nutrition(meal("밥", ntr=NTR, cal="844.5 Kcal"))
        self.assertEqual(values["단백질(g)"], "34.5")
        self.assertEqual(values["열량"], "844.5")
        self.assertNotIn("철분(mg)", values)

    def test_broken_or_empty(self):
        self.assertEqual(core.parse_nutrition(None), {})
        self.assertEqual(core.parse_nutrition(meal("밥", ntr="이상한 값")), {})

    def test_text_uses_real_value(self):
        nutrition = core._knowledge_tiers(meal("이름모를메뉴", ntr=NTR))[2]
        self.assertIn("오늘 급식의 단백질은 34.5g이에요. 몸의 조직을 만드는 영양소예요.", nutrition)


class MealSummary(unittest.TestCase):
    TODAY = "탄수화물(g) : 90.0<br/>단백질(g) : 34.5<br/>지방(g) : 36.7"

    def test_ratio_of_real_meal(self):
        self.assertEqual(core.macro_ratio(meal("밥", ntr=self.TODAY)), (43, 17, 40))

    def test_ratio_always_totals_100(self):
        for carbs, protein, fat in ((90, 34, 36), (101, 27.9, 34.1), (1, 1, 1), (70, 30, 30)):
            ntr = f"탄수화물(g) : {carbs}<br/>단백질(g) : {protein}<br/>지방(g) : {fat}"
            self.assertEqual(sum(core.macro_ratio(meal("밥", ntr=ntr))), 100)

    def test_ratio_needs_all_three(self):
        self.assertIsNone(core.macro_ratio(meal("밥", ntr="탄수화물(g) : 90.0")))
        self.assertIsNone(core.macro_ratio(None))

    def test_summary(self):
        summary = core.meal_summary(meal("밥", ntr=self.TODAY, cal="844.5 Kcal"))
        self.assertEqual(summary, "845 kcal\n탄단지 43:17:40")

    def test_summary_with_partial_data(self):
        self.assertEqual(core.meal_summary(meal("밥", cal="700 Kcal")), "700 kcal")
        self.assertEqual(core.meal_summary(meal("밥", ntr=self.TODAY)), "탄단지 43:17:40")
        self.assertEqual(core.meal_summary(meal("밥")), "")

    def test_summary_has_no_percent_or_judgement(self):
        summary = core.meal_summary(meal("밥", ntr=self.TODAY, cal="844.5 Kcal"))
        for word in ("%", "권장", "높음", "낮음", "좋음", "나쁨"):
            self.assertNotIn(word, summary)


class Selection(unittest.TestCase):
    def test_menu_first(self):
        text = core.select_knowledge(meal("미역국", ntr=NTR), MENU_FIRST_DAY)
        self.assertIn("미역", text)

    def test_nutrition_first_every_third_day(self):
        text = core.select_knowledge(meal("미역국", ntr=NTR), NUTRITION_FIRST_DAY)
        self.assertIn("오늘 급식", text)

    def test_all_nutrition_cards_appear_over_time(self):
        ntr = ("탄수화물(g) : 90<br/>단백질(g) : 34<br/>지방(g) : 36<br/>비타민A(R.E) : 82<br/>"
               "티아민(mg) : 0.8<br/>리보플라빈(mg) : 0.6<br/>비타민C(mg) : 5<br/>"
               "칼슘(mg) : 173<br/>철분(mg) : 2.7")
        today = meal("미역국", ntr=ntr)
        shown = set()
        for offset in range(0, 90, 3):
            shown.add(core.select_knowledge(today, NUTRITION_FIRST_DAY + timedelta(days=offset)))
        self.assertEqual(len(shown), len(cards.NUTRITION_TEMPLATES))

    def test_falls_back_to_nutrition(self):
        text = core.select_knowledge(meal("처음보는메뉴", ntr=NTR), MENU_FIRST_DAY)
        self.assertIn("오늘 급식", text)

    def test_falls_back_to_general_without_data(self):
        self.assertIn(core.select_knowledge(meal("처음보는메뉴"), MENU_FIRST_DAY), cards.GENERAL_CARDS)

    def test_no_meal_day(self):
        self.assertIn(core.select_knowledge(None, MENU_FIRST_DAY), cards.GENERAL_CARDS)

    def test_same_day_is_stable(self):
        today = meal("미역국", "배추김치", "제육볶음", ntr=NTR)
        self.assertEqual(core.select_knowledge(today, MENU_FIRST_DAY),
                         core.select_knowledge(today, MENU_FIRST_DAY))

    def test_avoids_yesterday(self):
        today = meal("배추김치", "배추겉절이", "현미밥")
        previous = meal("배추김치", "배추겉절이", "현미밥")
        for offset in range(30):
            day = MENU_FIRST_DAY + timedelta(days=offset)
            self.assertNotEqual(
                core.select_knowledge(today, day, previous),
                core.select_knowledge(previous, day - timedelta(days=1)),
            )

    def test_single_candidate_is_allowed_to_repeat(self):
        today = meal("미역국")
        day = MENU_FIRST_DAY + timedelta(days=1)
        self.assertIn("미역", core.select_knowledge(today, day, today))


class CardData(unittest.TestCase):
    def all_cards(self):
        return cards.MENU_CARDS + cards.CATEGORY_CARDS

    def test_every_card_has_a_trigger_and_text(self):
        for card in self.all_cards():
            self.assertTrue(card.get("keywords") or card.get("endings"), card)
            self.assertTrue(card["text"], card)

    def test_tuples_not_strings(self):
        for card in self.all_cards():
            for field in ("keywords", "endings", "exclude"):
                self.assertNotIsInstance(card.get(field, ()), str, card)

    def test_no_duplicate_text(self):
        texts = [card["text"] for card in self.all_cards()]
        self.assertEqual(len(texts), len(set(texts)))

    def test_templates_have_value(self):
        for card in cards.NUTRITION_TEMPLATES:
            self.assertIn("{value}", card["text"])

    def test_short_enough_for_two_lines(self):
        for card in self.all_cards() + cards.NUTRITION_TEMPLATES:
            self.assertLessEqual(len(card["text"]), 50, card["text"])


if __name__ == "__main__":
    unittest.main()
