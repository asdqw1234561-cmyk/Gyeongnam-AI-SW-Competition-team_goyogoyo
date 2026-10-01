"""
편의점 수집 스크립트·조회 서비스 오프라인 테스트 (실제 API를 호출하지 않음).

    python -m unittest tests.test_convenience -v

가짜(mock) 응답으로 페이지 순회, 편의점 필터, 구 이름 검증, 오류 시 '미확보' 처리,
CSV 저장 -> services.convenience 조회까지의 흐름만 검증한다.
여기 나오는 업소/숫자는 테스트용이며 실제 데이터가 아니다.
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import collect_convenience_stores as col  # noqa: E402
from services import convenience as svc  # noqa: E402


def _item(i, signgu_cd, signgu_nm, scls="편의점"):
    return {"bizesId": f"{signgu_cd}-{i}", "bizesNm": f"테스트상호{i}", "brchNm": "",
            "indsSclsNm": scls, "signguCd": signgu_cd, "signguNm": signgu_nm,
            "rdnmAdr": f"경상남도 창원시 {signgu_nm} 테스트로 {i}", "lnoAdr": "",
            "adongNm": "테스트동", "lon": "128.6", "lat": "35.2"}


def fake_call(operation, params, key):
    cd, page = params["key"], params["pageNo"]
    if cd == "48129":
        raise col.ApiError("resultCode=30 resultMsg=SERVICE_KEY_IS_NOT_REGISTERED_ERROR")
    name = "창원시 " + col.CHANGWON_DISTRICTS[cd][1]
    pages = {
        1: [_item(1, cd, name), _item(2, cd, name, scls="슈퍼마켓")],
        2: [_item(3, cd, name), _item(1, cd, name)],  # 중복 bizesId 1
    }
    if cd == "48123":  # 구 이름이 다른 레코드는 제외돼야 함
        pages[2].append(_item(9, cd, "창원시 의창구"))
    items = pages.get(page, [])
    return {"header": {"resultCode": "00", "stdrYm": "202506"},
            "body": {"items": items, "totalCount": 5, "numOfRows": 3, "pageNo": page}}


class CollectTest(unittest.TestCase):
    def test_collect_and_query(self):
        with mock.patch.object(col, "_call", side_effect=fake_call), \
             mock.patch.object(col.time, "sleep"):
            stores, counts = col.collect("dummy", None, "편의점", num_of_rows=3, max_pages=10)

        by_id = {c["region_id"]: c for c in counts}
        self.assertEqual(by_id["CW-UICHANG"]["count"], 2)       # 1,3 (슈퍼마켓·중복 제외)
        self.assertEqual(by_id["CW-SEONGSAN"]["count"], 2)      # 구 불일치 1건 제외
        self.assertIn("불일치 1건", by_id["CW-SEONGSAN"]["note"])
        self.assertEqual(by_id["CW-JINHAE"]["data_status"], "미확보")
        self.assertEqual(by_id["CW-JINHAE"]["count"], "")
        self.assertEqual(by_id["CW-UICHANG"]["reference_ym"], "202506")

        with tempfile.TemporaryDirectory() as tmp:
            stores_csv = os.path.join(tmp, "s.csv")
            counts_csv = os.path.join(tmp, "c.csv")
            col._write_csv(stores_csv, col.STORE_COLUMNS, stores)
            col._write_csv(counts_csv, col.COUNT_COLUMNS, counts + col._mart_not_secured_rows())
            with mock.patch.object(svc, "STORES_CSV", stores_csv), \
                 mock.patch.object(svc, "COUNTS_CSV", counts_csv):
                rows = svc.get_district_counts()
                self.assertEqual(len(rows), 5)
                self.assertEqual(svc.get_district_count("창원시 의창구")["value"], 2)
                self.assertIsNone(svc.get_district_count("진해")["value"])
                self.assertEqual(svc.get_district_count("진해구")["data_status"], "미확보")
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


if __name__ == "__main__":
    unittest.main()
