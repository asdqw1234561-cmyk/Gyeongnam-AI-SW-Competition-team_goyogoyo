"""
편의점 수집 스크립트·조회 서비스 오프라인 테스트 (실제 API를 호출하지 않음).

    python -m unittest tests.test_convenience -v

가짜(mock) 응답과 임시 CSV로 아래를 검증한다. 여기 나오는 업소·좌표는 테스트용이며 실제 데이터가 아니다.
  - 수집: 페이지 순회, 편의점 필터, 중복 제거, 구 이름 검증
  - 수집 완전성: 중간 페이지 실패 / max_pages 도달 / 빈 페이지 / totalCount 변경 시 '미확보'
  - 조회: 구별 집계, 구별 목록
  - 주변 조회: 300m·500m·1km 반경, 직선거리 정렬, 전체 수 vs 표시 목록, 잘못된 입력·데이터 없음
"""

import csv
import math
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import collect_convenience_stores as col  # noqa: E402
from services import convenience as svc  # noqa: E402


# ------------------------------------------------------------------ 수집용 가짜 API
def _item(i, signgu_cd, signgu_nm, scls="편의점"):
    return {"bizesId": f"{signgu_cd}-{i}", "bizesNm": f"테스트상호{i}", "brchNm": "",
            "indsSclsNm": scls, "signguCd": signgu_cd, "signguNm": signgu_nm,
            "rdnmAdr": f"경상남도 창원시 {signgu_nm} 테스트로 {i}", "lnoAdr": "",
            "adongNm": "테스트동", "lon": "128.6", "lat": "35.2"}


def _page(items, total, page):
    return {"header": {"resultCode": "00", "stdrYm": "202606"},
            "body": {"items": items, "totalCount": total, "numOfRows": 3, "pageNo": page}}


def fake_call(operation, params, key):
    """구마다 totalCount=5, 3건/페이지. 진해구는 인증 오류."""
    cd, page = params["key"], params["pageNo"]
    if cd == "48129":
        raise col.ApiError("resultCode=30 resultMsg=SERVICE_KEY_IS_NOT_REGISTERED_ERROR")
    name = "창원시 " + col.CHANGWON_DISTRICTS[cd][1]
    pages = {
        1: [_item(1, cd, name), _item(2, cd, name, scls="슈퍼마켓"), _item(3, cd, name)],
        2: [_item(1, cd, name)],  # 중복 bizesId 1
    }
    if cd == "48123":  # 구 이름이 다른 레코드는 제외돼야 함
        pages[2].append(_item(9, cd, "창원시 의창구"))
    else:
        pages[2].append(_item(4, cd, name))
    return _page(pages.get(page, []), 5, page)


def _no_sleep():
    return mock.patch.object(col.time, "sleep")


class CollectTest(unittest.TestCase):
    def test_collect_and_query(self):
        with mock.patch.object(col, "_call", side_effect=fake_call), _no_sleep():
            stores, counts = col.collect("dummy", None, "편의점", num_of_rows=3, max_pages=10)

        by_id = {c["region_id"]: c for c in counts}
        self.assertEqual(by_id["CW-UICHANG"]["count"], 3)       # 1,3,4 (슈퍼마켓·중복 제외)
        self.assertEqual(by_id["CW-UICHANG"]["data_status"], "확보")
        self.assertEqual(by_id["CW-SEONGSAN"]["count"], 2)      # 구 불일치 1건 제외
        self.assertIn("불일치 1건", by_id["CW-SEONGSAN"]["note"])
        self.assertEqual(by_id["CW-JINHAE"]["data_status"], "미확보")
        self.assertEqual(by_id["CW-JINHAE"]["count"], "")
        self.assertEqual(by_id["CW-UICHANG"]["reference_ym"], "202606")

        with tempfile.TemporaryDirectory() as tmp:
            stores_csv = os.path.join(tmp, "s.csv")
            counts_csv = os.path.join(tmp, "c.csv")
            col._write_csv(stores_csv, col.STORE_COLUMNS, stores)
            col._write_csv(counts_csv, col.COUNT_COLUMNS, counts + col._mart_not_secured_rows())
            with mock.patch.object(svc, "STORES_CSV", stores_csv), \
                 mock.patch.object(svc, "COUNTS_CSV", counts_csv):
                self.assertEqual(len(svc.get_district_counts()), 5)
                self.assertEqual(svc.get_district_count("창원시 의창구")["value"], 3)
                self.assertIsNone(svc.get_district_count("진해")["value"])
                self.assertTrue(all(r["data_status"] == "미확보"
                                    for r in svc.get_district_counts("대형마트")))
                self.assertEqual(len(svc.get_stores_by_district("CW-SEONGSAN")), 2)
                self.assertEqual(len(svc.get_stores_by_district("성산구", keyword="상호3")), 1)

    def test_no_data_yet(self):
        with mock.patch.object(svc, "STORES_CSV", "/nonexistent/a.csv"), \
             mock.patch.object(svc, "COUNTS_CSV", "/nonexistent/b.csv"):
            rows = svc.get_district_counts()
            self.assertEqual(len(rows), 5)
            self.assertTrue(all(r["data_status"] == "미확보" and r["value"] is None for r in rows))
            self.assertEqual(svc.get_stores_by_district("의창구"), [])


class CompletenessTest(unittest.TestCase):
    """페이지 누락이 있으면 '확보'로 저장되지 않아야 한다."""

    def _fetch(self, side_effect, max_pages=10, retries=0):
        with mock.patch.object(col, "_call", side_effect=side_effect), _no_sleep():
            return col.fetch_district("dummy", "48121", None, 3, max_pages,
                                      retries=retries, retry_wait=0)

    @staticmethod
    def _items(n, start=0):
        return [_item(start + i, "48121", "창원시 의창구") for i in range(n)]

    def test_complete(self):
        r = self._fetch(lambda o, p, k: _page(self._items(3 if p["pageNo"] == 1 else 2,
                                                          start=p["pageNo"] * 10), 5, p["pageNo"]))
        self.assertTrue(r.complete, r.reason)
        self.assertEqual(len(r.items), 5)

    def test_zero_records_is_complete(self):
        r = self._fetch(lambda o, p, k: _page([], 0, p["pageNo"]))
        self.assertTrue(r.complete)

    def test_middle_page_failure(self):
        def side(o, p, k):
            if p["pageNo"] == 2:
                raise col.ApiError("HTTP 500")
            return _page(self._items(3), 7, p["pageNo"])
        r = self._fetch(side)
        self.assertFalse(r.complete)
        self.assertIn("페이지 조회 실패", r.reason)

    def test_retry_then_success(self):
        calls = {"n": 0}

        def side(o, p, k):
            calls["n"] += 1
            if calls["n"] == 2:  # 2페이지 첫 시도만 실패
                raise col.ApiError("일시 오류")
            return _page(self._items(3 if p["pageNo"] == 1 else 1, start=p["pageNo"] * 10),
                         4, p["pageNo"])
        r = self._fetch(side, retries=1)
        self.assertTrue(r.complete, r.reason)

    def test_max_pages_reached(self):
        r = self._fetch(lambda o, p, k: _page(self._items(3, start=p["pageNo"] * 10), 30,
                                              p["pageNo"]), max_pages=2)
        self.assertFalse(r.complete)
        self.assertIn("max_pages", r.reason)

    def test_empty_page_before_total(self):
        r = self._fetch(lambda o, p, k: _page(self._items(3) if p["pageNo"] == 1 else [], 7,
                                              p["pageNo"]))
        self.assertFalse(r.complete)
        self.assertIn("비어 있음", r.reason)

    def test_total_count_changed(self):
        r = self._fetch(lambda o, p, k: _page(self._items(3, start=p["pageNo"] * 10),
                                              7 if p["pageNo"] == 1 else 8, p["pageNo"]))
        self.assertFalse(r.complete)
        self.assertIn("totalCount 변경", r.reason)

    def test_missing_total_count(self):
        r = self._fetch(lambda o, p, k: {"header": {"resultCode": "00"},
                                         "body": {"items": self._items(2)}})
        self.assertFalse(r.complete)

    def test_collect_marks_incomplete_district_not_secured(self):
        def side(o, p, k):
            if p["key"] == "48127" and p["pageNo"] == 2:
                raise col.ApiError("timeout")
            return fake_call(o, p, k)
        with mock.patch.object(col, "_call", side_effect=side), _no_sleep():
            stores, counts = col.collect("dummy", None, "편의점", 3, 10, retries=0, retry_wait=0)
        row = next(c for c in counts if c["region_id"] == "CW-MASANHOEWON")
        self.assertEqual(row["data_status"], "미확보")
        self.assertEqual(row["count"], "")
        self.assertIn("수집 불완전", row["note"])
        self.assertFalse(any(s["region_id"] == "CW-MASANHOEWON" for s in stores))

    def test_save_refused_when_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            stores_csv, counts_csv = os.path.join(tmp, "s.csv"), os.path.join(tmp, "c.csv")
            with open(counts_csv, "w", encoding="utf-8") as f:
                f.write("기존 데이터")
            with mock.patch.object(col, "_call", side_effect=fake_call), _no_sleep(), \
                 mock.patch.object(col, "STORES_CSV", stores_csv), \
                 mock.patch.object(col, "COUNTS_CSV", counts_csv), \
                 mock.patch.dict(os.environ, {col.SERVICE_KEY_ENV: "dummy"}), \
                 mock.patch("builtins.print"):
                with self.assertRaises(SystemExit) as ctx:
                    col.main(["collect", "--save", "--num-of-rows", "3", "--retries", "0"])
                self.assertEqual(ctx.exception.code, 2)
            with open(counts_csv, encoding="utf-8") as f:
                self.assertEqual(f.read(), "기존 데이터")  # 기존 CSV를 덮어쓰지 않음
            self.assertFalse(os.path.exists(stores_csv))


# ------------------------------------------------------------------ 주변 편의점 조회
CENTER = (35.2280, 128.6811)  # 테스트 기준점


def _offset(north_m=0.0, east_m=0.0):
    """기준점에서 북/동쪽으로 이동한 좌표(근사)."""
    lat = CENTER[0] + north_m / 111_320
    lon = CENTER[1] + east_m / (111_320 * math.cos(math.radians(CENTER[0])))
    return lat, lon


# (이름, 북쪽 m, 동쪽 m) - 거리 약 100, 250, 400, 450, 800, 1500m
_FIXTURE = [("C편의점", 0, 400), ("A편의점", 100, 0), ("B편의점", 0, -250),
            ("D편의점", -450, 0), ("E편의점", 800, 0), ("F편의점", 0, 1500)]


def _write_store_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=col.STORE_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({**{c: "" for c in col.STORE_COLUMNS}, **r})


class NearbyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.csv = os.path.join(self.tmp.name, "stores.csv")
        rows = []
        for name, n, e in _FIXTURE:
            lat, lon = _offset(n, e)
            rows.append({"facility_name": name, "district": "성산구", "lat": f"{lat:.7f}",
                         "lon": f"{lon:.7f}", "road_address": f"테스트로 {name}",
                         "source": "테스트", "reference_ym": "202606"})
        rows.append({"facility_name": "좌표없음", "lat": "", "lon": ""})
        rows.append({"facility_name": "좌표오류", "lat": "abc", "lon": "999"})
        _write_store_csv(self.csv, rows)
        self.patch = mock.patch.object(svc, "STORES_CSV", self.csv)
        self.patch.start()
        svc._coords_cache["key"] = None

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()
        svc._coords_cache["key"] = None

    def test_preset_radii(self):
        lat, lon = CENTER
        expected = {300: 2, 500: 4, 1000: 5}
        for radius, count in expected.items():
            r = svc.find_nearby_stores(lat, lon, radius_m=radius, max_results=10)
            self.assertEqual(r["status"], "ok")
            self.assertEqual(r["total_count"], count, radius)
        self.assertEqual(svc.count_nearby_by_radius(lat, lon)["counts"], expected)

    def test_sorted_and_fields(self):
        r = svc.find_nearby_stores(*CENTER, radius_m=1000, max_results=10)
        names = [s["facility_name"] for s in r["stores"]]
        self.assertEqual(names, ["A편의점", "B편의점", "C편의점", "D편의점", "E편의점"])
        dists = [s["straight_distance_m"] for s in r["stores"]]
        self.assertEqual(dists, sorted(dists))
        for got, want in zip(dists, [100, 250, 400, 450, 800]):
            self.assertLessEqual(abs(got - want), 3)
        first = r["stores"][0]
        for key in ("facility_name", "road_address", "lat", "lon", "straight_distance_m"):
            self.assertIn(key, first)
        self.assertEqual(r["distance_type"], "직선거리")
        self.assertIn("직선거리", r["message"])
        self.assertIn("도보거리", r["distance_note"])  # '...이 아닙니다' 안내 포함
        self.assertEqual(r["reference_date"], "202606")

    def test_total_vs_displayed(self):
        r = svc.find_nearby_stores(*CENTER, radius_m=1000, max_results=2)
        self.assertEqual(r["total_count"], 5)
        self.assertEqual(r["displayed_count"], 2)
        self.assertEqual([s["rank"] for s in r["stores"]], [1, 2])
        self.assertIn("2개만 표시", r["message"])

    def test_bad_rows_skipped_with_warning(self):
        r = svc.find_nearby_stores(*CENTER, radius_m=5000, max_results=100)
        self.assertEqual(r["total_count"], 6)
        self.assertTrue(any("2건" in w for w in r["warnings"]))

    def test_no_store_in_radius(self):
        lat, lon = _offset(3000, 3000)
        r = svc.find_nearby_stores(lat, lon, radius_m=300, max_results=5)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["total_count"], 0)
        self.assertEqual(r["stores"], [])

    def test_invalid_inputs_do_not_raise(self):
        cases = [
            (None, 128.68, 500, 10), ("abc", 128.68, 500, 10), (float("nan"), 128.68, 500, 10),
            (135.2, 28.68, 500, 10), (35.2, 200, 500, 10), (True, 128.68, 500, 10),
            (35.2, 128.68, 0, 10), (35.2, 128.68, -300, 10), (35.2, 128.68, 99999, 10),
            (35.2, 128.68, "반경", 10), (35.2, 128.68, 500, 0), (35.2, 128.68, 500, 2.5),
            (35.2, 128.68, 500, None), (35.2, 128.68, 500, 1000),
        ]
        for args in cases:
            r = svc.find_nearby_stores(*args)
            self.assertEqual(r["status"], "invalid_input", args)
            self.assertEqual(r["stores"], [])
            self.assertEqual(r["total_count"], 0)
        bad = svc.count_nearby_by_radius("x", "y")
        self.assertEqual(bad["status"], "invalid_input")
        self.assertEqual(bad["counts"], {300: None, 500: None, 1000: None})

    def test_string_numbers_accepted(self):
        r = svc.find_nearby_stores(str(CENTER[0]), str(CENTER[1]), "500", "3")
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["total_count"], 4)
        self.assertEqual(r["displayed_count"], 3)

    def test_outside_changwon_warns(self):
        r = svc.find_nearby_stores(37.5665, 126.9780, 1000, 5)  # 서울시청
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["total_count"], 0)
        self.assertTrue(any("창원시 범위" in w for w in r["warnings"]))

    def test_no_data_file(self):
        with mock.patch.object(svc, "STORES_CSV", "/nonexistent/x.csv"):
            r = svc.find_nearby_stores(*CENTER, 500, 10)
        self.assertEqual(r["status"], "no_data")
        self.assertEqual(r["stores"], [])

    def test_csv_without_coordinate_columns(self):
        with open(self.csv, "w", encoding="utf-8-sig") as f:
            f.write("facility_name\nA\n")
        svc._coords_cache["key"] = None
        r = svc.find_nearby_stores(*CENTER, 500, 10)
        self.assertEqual(r["status"], "no_data")

    def test_cache_reloads_when_file_changes(self):
        self.assertEqual(svc.find_nearby_stores(*CENTER, 300, 10)["total_count"], 2)
        lat, lon = _offset(10, 0)
        _write_store_csv(self.csv, [{"facility_name": "Z", "lat": f"{lat}", "lon": f"{lon}"}])
        os.utime(self.csv, (time.time() + 5, time.time() + 5))
        self.assertEqual(svc.find_nearby_stores(*CENTER, 300, 10)["total_count"], 1)


if __name__ == "__main__":
    unittest.main()
