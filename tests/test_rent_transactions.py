"""
G4-A 임대차 실거래 수집·정규화·분석 스크립트 테스트. 네트워크를 쓰지 않는다(requests/_call 모킹, 고정 픽스처).

    python -m unittest tests.test_rent_transactions -v
"""

import unittest
from unittest import mock

from scripts import analyze_rent_transactions as an
from scripts import collect_rent_transactions as col

# 공식 기술문서(아파트 전월세 실거래가 자료 기술문서.hwp) 응답 예제 형식 그대로 + 월세 1건
APT_XML = """<response><header><resultCode>000</resultCode><resultMsg>OK</resultMsg></header><body><items>
<item><aptNm>두산</aptNm><aptSeq>48123-34</aptSeq><buildYear>1999</buildYear><contractTerm> </contractTerm>
<contractType> </contractType><dealDay>20</dealDay><dealMonth>7</dealMonth><dealYear>2026</dealYear>
<deposit>50,000</deposit><excluUseAr>59.95</excluUseAr><floor>3</floor><jibun>232</jibun><monthlyRent>0</monthlyRent>
<sggCd>48123</sggCd><umdNm>상남동</umdNm><useRRRight> </useRRRight></item>
<item><aptNm>대동</aptNm><aptSeq>48123-77</aptSeq><buildYear>2005</buildYear><contractType>신규</contractType>
<dealDay>3</dealDay><dealMonth>8</dealMonth><dealYear>2026</dealYear><deposit>1,000</deposit><excluUseAr>84.9</excluUseAr>
<jibun>11</jibun><monthlyRent>65</monthlyRent><sggCd>48123</sggCd><umdNm>중앙동</umdNm></item>
</items><numOfRows>10</numOfRows><pageNo>1</pageNo><totalCount>2</totalCount></body></response>""".encode()

KEY_ERROR_XML = """<OpenAPI_ServiceResponse><cmmMsgHeader><returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg>
<returnReasonCode>30</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>""".encode()


class ParseAndNormalizeTest(unittest.TestCase):

    def test_parse_official_example(self):
        total, items = col.parse_response(APT_XML)
        self.assertEqual(total, 2)
        self.assertEqual(items[0]["excluUseAr"], "59.95")
        self.assertEqual(items[0]["deposit"], "50,000")

    def test_key_error_raises_without_secret(self):
        with self.assertRaises(col.ApiError) as ctx:
            col.parse_response(KEY_ERROR_XML)
        self.assertIn("resultCode 30", str(ctx.exception))

    def test_jeonse_and_monthly_normalization(self):
        _, items = col.parse_response(APT_XML)
        jeonse, _ = col.normalize_item(items[0], "apartment", "48123")
        monthly, _ = col.normalize_item(items[1], "apartment", "48123")
        self.assertEqual((jeonse["region_id"], jeonse["rent_type"], jeonse["deposit_manwon"]),
                         ("CW-SEONGSAN", "jeonse", 50000))
        self.assertEqual((monthly["rent_type"], monthly["monthly_rent_manwon"], monthly["deposit_manwon"]),
                         ("monthly", 65, 1000))
        self.assertEqual(jeonse["contract_ym"], "202607")
        self.assertEqual(jeonse["contract_date"], "2026-07-20")
        self.assertEqual(jeonse["complex_key"], "48123-34")
        self.assertEqual(jeonse["exclusive_area_m2"], 59.95)
        self.assertEqual(monthly["contract_type_raw"], "신규")
        self.assertIn("15126474", jeonse["source"])

    def test_detached_has_no_exclusive_area(self):
        item = {"sggCd": "48129", "umdNm": "석동", "houseType": "다가구", "totalFloorAr": "210.5", "dealYear": "2026",
                "dealMonth": "5", "dealDay": "1", "deposit": "3,000", "monthlyRent": "35", "buildYear": "1995"}
        row, _ = col.normalize_item(item, "detached_multi", "48129")
        self.assertIsNone(row["exclusive_area_m2"])  # 연면적을 전용면적처럼 쓰지 않는다
        self.assertEqual(row["total_floor_area_m2"], 210.5)
        self.assertEqual(row["housing_subtype"], "다가구")

    def test_unusable_items_are_dropped_not_guessed(self):
        base = {"sggCd": "48121", "dealYear": "2026", "dealMonth": "1", "deposit": "100", "monthlyRent": "10"}
        self.assertEqual(col.normalize_item({**base, "sggCd": "48123"}, "apartment", "48121"), (None, "sggCd 불일치"))
        self.assertEqual(col.normalize_item({**base, "deposit": ""}, "apartment", "48121"), (None, "금액 해석 불가"))
        self.assertEqual(col.normalize_item({**base, "dealMonth": "x"}, "apartment", "48121"), (None, "계약일 해석 불가"))

    def test_month_range(self):
        self.assertEqual(col.month_range("202602", 4), ["202511", "202512", "202601", "202602"])


class FetchTest(unittest.TestCase):

    def test_paging_until_total(self):
        pages = {1: (3, [{"a": 1}, {"a": 2}]), 2: (3, [{"a": 3}])}
        with mock.patch.object(col, "_call", side_effect=lambda t, l, y, page, k, to: pages[page]), \
                mock.patch.object(col.time, "sleep"):
            self.assertEqual(len(col.fetch_month("apartment", "48121", "202601", "k")), 3)

    def test_incomplete_collection_is_error(self):
        with mock.patch.object(col, "_call", return_value=(5, [])), mock.patch.object(col.time, "sleep"):
            with self.assertRaises(col.ApiError):
                col.fetch_month("apartment", "48121", "202601", "k")

    def test_key_error_stops_collection(self):
        with mock.patch.object(col, "_call", side_effect=col.ApiError("resultCode 30 등록되지 않은 서비스키")), \
                mock.patch.object(col.time, "sleep"), mock.patch("builtins.print"):
            with self.assertRaises(col.ApiError):
                col.collect("k", ["202601"], ["apartment"])

    def test_transient_failure_marks_cell_missing_and_continues(self):
        def fake(t, lawd, ymd, page, k, to):
            if lawd == "48125":
                raise col.ApiError("resultCode 01 Application Error")
            return col.parse_response(APT_XML) if lawd == "48123" else (0, [])
        with mock.patch.object(col, "_call", side_effect=fake), mock.patch.object(col.time, "sleep"), \
                mock.patch("builtins.print"):
            rows, manifest = col.collect("k", ["202607"], ["apartment"], retry_wait=0)
        self.assertEqual(len(rows), 2)
        missing = [c for c in manifest["cells"] if c["status"] == "미확보"]
        self.assertEqual([c["region_id"] for c in missing], ["CW-MASANHAPPO"])
        self.assertEqual(manifest["contract_type_values"]["apartment"], {"": 1, "신규": 1})

    def test_network_exception_message_hides_url(self):
        with mock.patch.object(col.requests, "get", side_effect=col.requests.ConnectionError("https://x?serviceKey=SECRET")):
            with self.assertRaises(col.ApiError) as ctx:
                col._call("apartment", "48121", "202601", 1, "SECRET", 1)
        self.assertNotIn("SECRET", str(ctx.exception))


def _row(region, ym, rent_type, deposit, rent=0, housing="apartment"):
    return {"region_name": region, "contract_ym": ym, "rent_type": rent_type, "housing_type": housing,
            "deposit_manwon": deposit, "monthly_rent_manwon": rent, "exclusive_area_m2": 59.0}


class AnalyzeTest(unittest.TestCase):

    def test_quantile_and_describe(self):
        s = an.describe([10, 20, 30, 40, 1000])
        self.assertEqual((s["n"], s["median"], s["p25"], s["p75"]), (5, 30, 20, 40))
        self.assertEqual(s["mean"], 220)
        self.assertEqual(s["extreme_high"], 1)  # 1000 > 40 + 3*20
        self.assertEqual(an.describe([]), {"n": 0})

    def test_windows_with_and_without_lag(self):
        self.assertEqual(an.window_months("202609", 6, False), ["202604", "202605", "202606", "202607", "202608", "202609"])
        self.assertEqual(an.window_months("202609", 6, True), ["202602", "202603", "202604", "202605", "202606", "202607"])

    def test_types_and_rent_types_are_never_mixed(self):
        rows = [_row("의창구", "202601", "jeonse", 20000), _row("의창구", "202601", "monthly", 1000, 50),
                _row("의창구", "202601", "monthly", 500, 30, housing="officetel")]
        stats = an.group_stats(rows, ["202601"])
        self.assertEqual(stats[("apartment", "전세 보증금")]["의창구"]["n"], 1)
        self.assertEqual(stats[("apartment", "월세 금액")]["의창구"]["median"], 50)
        self.assertEqual(stats[("apartment", "월세 보증금")]["의창구"]["median"], 1000)
        self.assertEqual(stats[("officetel", "월세 금액")]["의창구"]["median"], 30)
        self.assertEqual(stats[("apartment", "전세 보증금")]["성산구"]["n"], 0)  # 표본 없는 구도 n=0으로 남긴다

    def test_adequacy_uses_configurable_min_sample(self):
        rows = [_row(name, "202601", "jeonse", 10000 + i) for name in an.REGION_ORDER for i in range(5)]
        stats = an.group_stats(rows, ["202601"])
        self.assertTrue(an.window_adequacy(stats, 5)[("apartment", "전세 보증금")]["comparable"])
        short = an.window_adequacy(stats, 6)[("apartment", "전세 보증금")]
        self.assertFalse(short["comparable"])
        self.assertEqual(short["min_n"], 5)

    def test_mean_vs_median_rank_change_detected(self):
        by_region = {"가": an.describe([10, 10, 10, 1000]), "나": an.describe([20, 20, 20, 20]),
                     "다": an.describe([30, 30, 30, 30])}
        rc = an.mean_vs_median_rank(by_region)
        self.assertEqual(rc["median_rank"]["가"], 1)
        self.assertEqual(rc["mean_rank"]["가"], 3)  # 극단값 하나로 평균 순위가 뒤집힘
        self.assertTrue(rc["changed"])
        self.assertIsNone(an.mean_vs_median_rank({"가": an.describe([])}))

    def test_low_price_monthly_share(self):
        rows = [_row("의창구", "202601", "monthly", 500, 25), _row("의창구", "202601", "monthly", 8000, 25),
                _row("의창구", "202601", "monthly", 1000, 60), _row("의창구", "202601", "jeonse", 9000)]
        out = an.low_price_monthly(rows, ["202601"])["apartment"]
        self.assertEqual((out["n_monthly"], out["rent_under_30"], out["below_report_threshold"]), (3, 2, 1))

    def test_full_report_from_fixture(self):
        rows = [_row(name, ym, "jeonse", 15000 + i * 100) for name in an.REGION_ORDER
                for ym in an.window_months("202609", 14, False) for i in range(3)]
        result = an.analyze(rows, "202609", 30)
        rec = an.recommend_window(result)
        self.assertEqual(rec[("apartment", "전세 보증금")], an.window_label(12, True))  # 6개월은 n=18 < 30
        report = an.to_markdown(result)
        self.assertIn("비교 가능한 기간", report)
        self.assertIn("전세 보증금", report)


class GyeongnamGroupTest(unittest.TestCase):
    """경남 22개 지역: 수집 대상은 22개, 분석은 같은 유형 그룹(창원시 구 / 시 / 군) 안에서만 비교한다."""

    def test_22_lawd_codes_with_changwon_ids_kept(self):
        self.assertEqual(len(col.DISTRICTS), 22)
        self.assertEqual(col.DISTRICTS["48121"], ("CW-UICHANG", "의창구"))
        self.assertEqual(col.DISTRICTS["48250"], ("GN-GIMHAE", "김해시"))
        self.assertEqual({k: len(v) for k, v in an.REGION_GROUPS.items()},
                         {"창원시 5개 구": 5, "경남 시 지역": 7, "경남 군 지역": 10})

    def test_group_analysis_only_compares_regions_in_the_group(self):
        rows = [_row(name, "202601", "jeonse", 10000 + i) for name in an.REGION_ORDER for i in range(3)]
        result = an.analyze(rows, "202603", 1, an.REGION_GROUPS["경남 군 지역"])
        regions = {name for w in result["windows"].values() for by_region in w["stats"].values() for name in by_region}
        self.assertEqual(regions, set(an.REGION_GROUPS["경남 군 지역"]))
        self.assertIn("경남 군 지역", an.to_markdown(result, "경남 군 지역"))


if __name__ == "__main__":
    unittest.main()
