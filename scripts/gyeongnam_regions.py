# 경상남도 비교 단위 22개(창원시 5개 구 + 17개 시·군) 마스터
"""
경남 확장 수집·집계 스크립트가 공통으로 쓰는 지역 목록과 코드 대응표.

비교 단위: 창원시는 기존처럼 5개 행정구, 나머지 17개 시·군은 시·군 단위 → 22개.
각 데이터는 서로 다른 코드 체계를 쓰므로 섞지 않고 여기서만 대응시킨다.
    sgis_cd      SGIS 시군구 경계(bnd_sigungu_00_2025_2Q.shp)의 SIGUNGU_CD
    lawd_cd      법정동코드 앞 5자리(data/raw/bjd_code.csv) - 상가정보 signguCd, 국토부 실거래 LAWD_CD
    name         행정안전부 주민등록 인구의 시군구명, 학교위치표준데이터 주소의 시군구 표기와 같은 이름
모든 코드는 원본 파일을 직접 열어 이름으로 확인한 값이다(추정 아님).
기존 창원시 5개 구의 region_id(CW-*)는 그대로 유지한다.
"""

from __future__ import annotations

PROVINCE = "경상남도"

# (region_id, name, region_type, sgis_cd, lawd_cd)
REGIONS: tuple[tuple[str, str, str, str, str], ...] = (
    ("CW-UICHANG", "창원시 의창구", "구", "38111", "48121"),
    ("CW-SEONGSAN", "창원시 성산구", "구", "38112", "48123"),
    ("CW-MASANHAPPO", "창원시 마산합포구", "구", "38113", "48125"),
    ("CW-MASANHOEWON", "창원시 마산회원구", "구", "38114", "48127"),
    ("CW-JINHAE", "창원시 진해구", "구", "38115", "48129"),
    ("GN-JINJU", "진주시", "시", "38030", "48170"),
    ("GN-TONGYEONG", "통영시", "시", "38050", "48220"),
    ("GN-SACHEON", "사천시", "시", "38060", "48240"),
    ("GN-GIMHAE", "김해시", "시", "38070", "48250"),
    ("GN-MIRYANG", "밀양시", "시", "38080", "48270"),
    ("GN-GEOJE", "거제시", "시", "38090", "48310"),
    ("GN-YANGSAN", "양산시", "시", "38100", "48330"),
    ("GN-UIRYEONG", "의령군", "군", "38510", "48720"),
    ("GN-HAMAN", "함안군", "군", "38520", "48730"),
    ("GN-CHANGNYEONG", "창녕군", "군", "38530", "48740"),
    ("GN-GOSEONG", "고성군", "군", "38540", "48820"),
    ("GN-NAMHAE", "남해군", "군", "38550", "48840"),
    ("GN-HADONG", "하동군", "군", "38560", "48850"),
    ("GN-SANCHEONG", "산청군", "군", "38570", "48860"),
    ("GN-HAMYANG", "함양군", "군", "38580", "48870"),
    ("GN-GEOCHANG", "거창군", "군", "38590", "48880"),
    ("GN-HAPCHEON", "합천군", "군", "38600", "48890"),
)

REGION_IDS = tuple(r[0] for r in REGIONS)
NAME_BY_ID = {r[0]: r[1] for r in REGIONS}
ID_BY_NAME = {r[1]: r[0] for r in REGIONS}
TYPE_BY_ID = {r[0]: r[2] for r in REGIONS}
ID_BY_SGIS = {r[3]: r[0] for r in REGIONS}
ID_BY_LAWD = {r[4]: r[0] for r in REGIONS}
