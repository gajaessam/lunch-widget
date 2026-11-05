import os
import sys
import unittest
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import lunch_core as core

ORDER = [str(i) for i in range(1, 12)]


class ParseMenuItem(unittest.TestCase):
    def check(self, raw, name, numbers, uncertain=False):
        self.assertEqual(core.parse_menu_item(raw), (name, numbers, uncertain))

    def test_neis_standard(self):
        self.check("#쌈장 (5.6.13)", "쌈장", ["5", "6", "13"])
        self.check("#삼겹살구이 (10)", "삼겹살구이", ["10"])

    def test_without_allergy(self):
        self.check("상추쌈 ", "상추쌈", [])
        self.check("샤인머스캣푸딩-50g ", "샤인머스캣푸딩-50g", [])

    def test_variant_notations(self):
        self.check("비빔밥 (1. 5. 6)", "비빔밥", ["1", "5", "6"])
        self.check("카레라이스(1,5,6)", "카레라이스", ["1", "5", "6"])
        self.check("돈까스 (1.2.5.6)(완제품)", "돈까스 (완제품)", ["1", "2", "5", "6"])
        self.check("우유 (2) 200ml", "우유 200ml", ["2"])
        self.check("쌀밥5.6.13.", "쌀밥", ["5", "6", "13"])
        self.check("김치(9.13)*", "김치", ["9", "13"])

    def test_non_code_text_is_kept(self):
        self.check("두부조림(저염)", "두부조림(저염)", [])
        self.check("연두부&양념장 (5.6.13)", "연두부&양념장", ["5", "6", "13"])

    def test_unknown_number_is_flagged_not_dropped(self):
        self.check("특식 (1.25)", "특식 (1.25)", [], True)
        self.check("특식 (100)", "특식 (100)", [], True)

    def test_format(self):
        self.assertEqual(core.format_allergies(["5", "6", "5"]), "알레르기: 대두·밀")
        self.assertEqual(core.format_allergies([]), "")


class Meals(unittest.TestCase):
    def test_lunch_is_preferred(self):
        rows = [{"MMEAL_SC_CODE": "1", "n": "조식"}, {"MMEAL_SC_CODE": "2", "n": "중식"}]
        self.assertEqual(core.select_meal(rows)["n"], "중식")

    def test_first_row_when_no_lunch(self):
        self.assertEqual(core.select_meal([{"MMEAL_SC_CODE": "3", "n": "석식"}])["n"], "석식")
        self.assertIsNone(core.select_meal([]))

    def test_api_rows(self):
        ok = {"mealServiceDietInfo": [{"head": []}, {"row": [{"a": 1}]}]}
        self.assertEqual(core.api_rows(ok, "mealServiceDietInfo"), [{"a": 1}])
        none = {"RESULT": {"CODE": "INFO-200"}}
        self.assertEqual(core.api_rows(none, "mealServiceDietInfo"), [])
        with self.assertRaises(ValueError):
            core.api_rows({"RESULT": {"CODE": "ERROR-500"}}, "mealServiceDietInfo")

    def test_meals_by_date(self):
        rows = [
            {"MLSV_YMD": "20261105", "MMEAL_SC_CODE": "1", "n": "조식"},
            {"MLSV_YMD": "20261105", "MMEAL_SC_CODE": "2", "n": "중식"},
            {"MLSV_YMD": "20261106", "MMEAL_SC_CODE": "2", "n": "다음날"},
        ]
        result = core.meals_by_date(rows)
        self.assertEqual(result["20261105"]["n"], "중식")
        self.assertEqual(result["20261106"]["n"], "다음날")

    def test_cache_keeps_recent(self):
        cache = {core.cache_key("B10", "1", f"2026{i:04d}"): i for i in range(101, 111)}
        pruned = core.prune_meal_cache(cache, limit=3)
        self.assertEqual(sorted(pruned.values()), [108, 109, 110])


class SchoolSearch(unittest.TestCase):
    def test_parse(self):
        response = {"schoolInfo": [
            {"head": [{"list_total_count": 29}, {"RESULT": {"CODE": "INFO-000"}}]},
            {"row": [{
                "ATPT_OFCDC_SC_CODE": "B10", "ATPT_OFCDC_SC_NM": "서울특별시교육청",
                "SD_SCHUL_CODE": "7091433", "SCHUL_NM": "서일중학교", "ORG_RDNMA": "서울 중랑구",
            }]},
        ]}
        schools, total = core.parse_school_search(response)
        self.assertEqual(total, 29)
        self.assertEqual(schools[0]["office_code"], "B10")
        self.assertEqual(schools[0]["school_code"], "7091433")
        self.assertEqual(schools[0]["label"], "서일중학교 · 서울특별시 · 서울 중랑구")

    def test_no_result(self):
        self.assertEqual(core.parse_school_search({"RESULT": {"CODE": "INFO-200"}}), ([], 0))


class Rotation(unittest.TestCase):
    def rule(self, cycle="weekly", base="2026-09-01", **extra):
        return {"base_order": ORDER, "cycle": cycle, "base_date": base, **extra}

    def test_weekly_keeps_existing_result(self):
        # 2026-09-01 주부터 9주 뒤 (11/5 평가 때 확인한 실제 값)
        order = core.rotate_order(self.rule(), date(2026, 11, 5))
        self.assertEqual(order, ['3', '4', '5', '6', '7', '8', '9', '10', '11', '1', '2'])

    def test_weekly_changes_on_monday(self):
        rule = self.rule()
        self.assertEqual(core.rotate_order(rule, date(2026, 11, 8))[0], '3')
        self.assertEqual(core.rotate_order(rule, date(2026, 11, 9))[0], '2')

    def test_monthly(self):
        order = core.rotate_order(self.rule("monthly"), date(2026, 11, 5))
        self.assertEqual(order[0], '10')

    def test_daily_skips_weekend(self):
        rule = self.rule("daily", "2026-11-02", day_basis="weekday")
        friday = core.rotate_order(rule, date(2026, 11, 6))
        saturday = core.rotate_order(rule, date(2026, 11, 7))
        monday = core.rotate_order(rule, date(2026, 11, 9))
        self.assertEqual(friday, saturday)
        self.assertEqual(ORDER.index(friday[0]) - ORDER.index(monday[0]), 1)

    def test_old_daily_rule_keeps_todays_order_after_migration(self):
        old = self.rule("daily", "2026-10-01")
        today = date(2026, 11, 5)
        new = core.modernize_daily_rule(old, today)
        self.assertEqual(new["day_basis"], "weekday")
        self.assertEqual(core.rotate_order(new, today), core.rotate_order(old, today))

    def test_other_rules_are_untouched(self):
        rule = self.rule()
        self.assertIs(core.modernize_daily_rule(rule, date(2026, 11, 5)), rule)

    def test_extends_default_order(self):
        order = core.rotate_order({"base_order": ORDER, "cycle": "weekly", "base_date": "2026-09-07"},
                                  date(2026, 9, 7), ensure_class=13)
        self.assertEqual(len(order), 13)

    def test_custom_order_is_not_extended(self):
        rule = {"base_order": ["1", "2"], "cycle": "weekly", "base_date": "2026-09-07"}
        self.assertEqual(len(core.rotate_order(rule, date(2026, 9, 7), ensure_class=13)), 2)

    def test_empty_and_broken(self):
        self.assertEqual(core.rotate_order({"base_order": []}, date(2026, 9, 7)), [])
        broken = {"base_order": ["1", "2"], "cycle": "???", "base_date": "오류"}
        self.assertEqual(len(core.rotate_order(broken, date(2026, 11, 5))), 2)

    def test_weekdays_between(self):
        self.assertEqual(core.weekdays_between(date(2026, 11, 6), date(2026, 11, 9)), 1)
        self.assertEqual(core.weekdays_between(date(2026, 11, 2), date(2026, 11, 16)), 10)
        self.assertEqual(core.weekdays_between(date(2026, 11, 9), date(2026, 11, 6)), -1)


class Departure(unittest.TestCase):
    def test_time(self):
        self.assertEqual(core.departure_minutes("12:30", 1, 2, 5), 750)
        self.assertEqual(core.departure_minutes("12:30", 9, 2, 5), 770)

    def test_bad_start_time_falls_back(self):
        self.assertEqual(core.departure_minutes("오류", 1, 2, 5), 750)


class Version(unittest.TestCase):
    def test_newer(self):
        self.assertTrue(core.is_newer_version("1.10.0", "1.9.9"))
        self.assertFalse(core.is_newer_version("1.5.6", "1.6.0"))
        self.assertFalse(core.is_newer_version("1.6.0", "1.6.0"))

    def test_invalid(self):
        self.assertFalse(core.is_newer_version("abc"))
        self.assertFalse(core.is_newer_version(None))


if __name__ == "__main__":
    unittest.main()
