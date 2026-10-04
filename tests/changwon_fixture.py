"""
설명 검증·후보 판정 '규칙'을 시험하는 테스트용 고정 데이터.

경남 확장(2026-10-04) 이전의 창원시 5개 구 시설 수(버스정류장 831/452/760/365/518, 의료기관 262/398/242/256/200,
편의점 199/245/159/151/196)를 그대로 고정한다. 인구 지표를 넣지 않았으므로 analysis.scoring은 '시설 수 그대로'
비교(basis=count)로 계산해, 이 테스트들이 전제한 상황(성산구 최적, Critic이 대안 마산합포구 → 의창구 교체,
의창구 버스정류장 831개로 가장 많음 등)이 데이터 갱신과 무관하게 재현된다.
실데이터 동작은 tests/test_scoring.py·tests/test_gyeongnam_data.py·tests/test_pages_smoke.py가 따로 확인한다.

사용: 테스트 모듈에서 `setUpModule = changwon_fixture.start; tearDownModule = changwon_fixture.stop`
"""

import copy
from unittest import mock

_VALUES = {
    "CW-UICHANG": ("의창구", 831, 262, 199),
    "CW-SEONGSAN": ("성산구", 452, 398, 245),
    "CW-MASANHAPPO": ("마산합포구", 760, 242, 159),
    "CW-MASANHOEWON": ("마산회원구", 365, 256, 151),
    "CW-JINHAE": ("진해구", 518, 200, 196),
}


def _indicator(code, name, category, value):
    return {"indicator_code": code, "indicator_name": name, "category": category, "value": str(value), "unit": "개",
            "source": "테스트 고정값", "reference_date": "fixture", "data_status": "확보", "note": None}


def changwon_count_regions(**_ignored) -> list[dict]:
    return [
        {"region_id": rid, "region_name": name, "city": "창원시", "region_type": "구",
         "categories": {
             "교통": [_indicator("bus_stop_count", "버스정류장 수", "교통", bus)],
             "의료": [_indicator("hospital_count", "의료기관 수", "의료", hospital)],
             "생활편의": [_indicator("convenience_store_count", "편의점 수", "생활편의", conv)],
         }}
        for rid, (name, bus, hospital, conv) in _VALUES.items()
    ]


_patches = []


def start() -> None:
    for target in ("analysis.scoring.get_all_regions", "agent.planner.get_all_regions"):
        p = mock.patch(target, side_effect=lambda *a, **k: copy.deepcopy(changwon_count_regions()))
        p.start()
        _patches.append(p)


def stop() -> None:
    while _patches:
        _patches.pop().stop()
