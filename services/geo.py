# 위치 기반 조회 공용 유틸 (services/bus_stops.py, services/convenience.py, pages/user.py 공통)
"""
버스정류장·편의점 주변 조회가 "같은 공식·같은 값"으로 거리를 재도록 공통 정의를 한곳에
둔다. 예전에는 두 서비스 모듈에 같은 코드가 복사돼 있었고, 이 파일로 옮기면서 값과
계산은 바꾸지 않았다.

거리는 두 좌표 사이의 **직선거리**(하버사인 공식)다. 도보거리나 이동시간이 아니다.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np

# 창원시 5개 구 (region_id -> 구 이름)
DISTRICTS = {
    "CW-UICHANG": "의창구",
    "CW-SEONGSAN": "성산구",
    "CW-MASANHAPPO": "마산합포구",
    "CW-MASANHOEWON": "마산회원구",
    "CW-JINHAE": "진해구",
}

# 창원시 대략 범위(검색 위치가 크게 벗어났는지 안내용, 계산을 막지는 않음)
CHANGWON_BBOX = {"lat": (34.9, 35.45), "lon": (128.35, 128.95)}

EARTH_RADIUS_M = 6_371_008.8


def to_float(value) -> Optional[float]:
    """유한한 숫자로 바꿀 수 있으면 float, 아니면 None(bool은 숫자로 보지 않는다)."""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def to_int(value) -> Optional[int]:
    """정수 값이면 int, 아니면 None(1.5 같은 소수는 None)."""
    number = to_float(value)
    if number is None or number != int(number):
        return None
    return int(number)


def haversine_m(lat: float, lon: float, lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    """기준점(lat, lon)에서 여러 좌표까지의 직선거리(m)."""
    p1, p2 = np.radians(lat), np.radians(lats)
    dphi = p2 - p1
    dlmb = np.radians(lons - lon)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_M * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


# 경상남도 대략 범위(검색 위치가 크게 벗어났는지 안내용, 계산을 막지는 않음)
GYEONGNAM_BBOX = {"lat": (34.4, 35.95), "lon": (127.55, 129.35)}


def is_within_gyeongnam_bbox(lat: float, lon: float) -> bool:
    return (
        GYEONGNAM_BBOX["lat"][0] <= lat <= GYEONGNAM_BBOX["lat"][1]
        and GYEONGNAM_BBOX["lon"][0] <= lon <= GYEONGNAM_BBOX["lon"][1]
    )


def is_within_changwon_bbox(lat: float, lon: float) -> bool:
    return (
        CHANGWON_BBOX["lat"][0] <= lat <= CHANGWON_BBOX["lat"][1]
        and CHANGWON_BBOX["lon"][0] <= lon <= CHANGWON_BBOX["lon"][1]
    )
