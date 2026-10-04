"""
위치 탐색(pages/user.py)의 "예시 위치" 목록 - 경상남도 22개 지역의 시·군·구청 부근 대표 근사 좌표.

[무엇인가]
- 검색을 시작하기 위한 예시 좌표일 뿐이다. 정확한 건물 출입구 좌표가 아니고, 특정 주거지 추천도 아니다.
- 범위(구 5곳 / 시 7곳 / 군 10곳)는 services.region_data.REGION_SCOPES와 같은 유형 구분을 쓴다.
- 좌표는 표시·검색 시작용으로만 쓰며, 반경 계산(하버사인)·시설 조회·지역 판정 로직은 바꾸지 않는다.

[좌표 기준 - docs/example_locations.md]
- 김해시청·진주시청: 기존 예시 좌표를 그대로 재사용했다.
- 그 외 20곳: OpenStreetMap(Nominatim, 2026-10-05 조회)의 해당 시·군·구청 객체 좌표를 소수 4자리로 반올림했다.
  프로젝트 버스정류장 데이터의 같은 이름 정류장(예: "통영시청", "함양군청", "의창구청")이 있는 곳은
  그 정류장과 수백 m 이내인지 대조했고, 모든 좌표가 SGIS 경계상 해당 지역 안에 있는지 테스트로 확인한다.
"""

from __future__ import annotations

# 범위 이름은 비교 범위 화면 표시 이름(services.region_data.REGION_SCOPE_DISPLAY)과 같게 맞춘다.
EXAMPLE_SCOPES: dict[str, str] = {
    "경남 구 지역 (5곳)": "구",
    "경남 시 지역 (7곳)": "시",
    "경남 군 지역 (10곳)": "군",
}

# (region_id, 세부 지역 이름, 대표 위치 이름, 위도, 경도) - 지역 순서는 data/regions.csv와 같다.
_ROWS: tuple[tuple[str, str, str, str, float, float], ...] = (
    ("구", "CW-UICHANG", "의창구", "의창구청", 35.2556, 128.6350),
    ("구", "CW-SEONGSAN", "성산구", "성산구청", 35.1985, 128.7025),
    ("구", "CW-MASANHAPPO", "마산합포구", "마산합포구청", 35.1971, 128.5678),
    ("구", "CW-MASANHOEWON", "마산회원구", "마산회원구청", 35.2209, 128.5797),
    ("구", "CW-JINHAE", "진해구", "진해구청", 35.1330, 128.7101),
    ("시", "GN-JINJU", "진주시", "진주시청", 35.1800, 128.1076),  # 기존 예시 좌표 재사용
    ("시", "GN-TONGYEONG", "통영시", "통영시청", 34.8541, 128.4334),
    ("시", "GN-SACHEON", "사천시", "사천시청", 35.0036, 128.0645),
    ("시", "GN-GIMHAE", "김해시", "김해시청", 35.2285, 128.8894),  # 기존 예시 좌표 재사용
    ("시", "GN-MIRYANG", "밀양시", "밀양시청", 35.5037, 128.7461),
    ("시", "GN-GEOJE", "거제시", "거제시청", 34.8805, 128.6213),
    ("시", "GN-YANGSAN", "양산시", "양산시청", 35.3350, 129.0370),
    ("군", "GN-UIRYEONG", "의령군", "의령군청", 35.3221, 128.2615),
    ("군", "GN-HAMAN", "함안군", "함안군청", 35.2725, 128.4066),
    ("군", "GN-CHANGNYEONG", "창녕군", "창녕군청", 35.5446, 128.4922),
    ("군", "GN-GOSEONG", "고성군", "고성군청", 34.9729, 128.3225),
    ("군", "GN-NAMHAE", "남해군", "남해군청", 34.8375, 127.8923),
    ("군", "GN-HADONG", "하동군", "하동군청", 35.0673, 127.7513),
    ("군", "GN-SANCHEONG", "산청군", "산청군청", 35.4156, 127.8735),
    ("군", "GN-HAMYANG", "함양군", "함양군청", 35.5205, 127.7252),
    ("군", "GN-GEOCHANG", "거창군", "거창군청", 35.6860, 127.9097),
    ("군", "GN-HAPCHEON", "합천군", "합천군청", 35.5667, 128.1658),
)

EXAMPLE_LOCATIONS: list[dict] = [
    {"region_type": t, "region_id": rid, "region_name": name,
     "label": f"{office} 부근 예시 위치 (근사 좌표)", "coord": (lat, lon)}
    for t, rid, name, office, lat, lon in _ROWS
]

DEFAULT_SCOPE = next(iter(EXAMPLE_SCOPES))
EXAMPLE_GUIDE = (
    "경상남도 22개 지역(구 5곳 · 시 7곳 · 군 10곳)의 대표 위치를 예시로 선택할 수 있습니다. "
    "대표 위치는 검색 시작을 위한 시·군·구청 부근 근사 좌표이며, 특정 주거지 추천을 의미하지 않습니다."
)


def locations_in_scope(scope_label: str) -> list[dict]:
    """예시 위치 범위(구/시/군) 안의 세부 지역 목록. 모르는 범위면 빈 목록."""
    region_type = EXAMPLE_SCOPES.get(scope_label)
    return [loc for loc in EXAMPLE_LOCATIONS if loc["region_type"] == region_type]


def default_location() -> dict:
    return locations_in_scope(DEFAULT_SCOPE)[0]
