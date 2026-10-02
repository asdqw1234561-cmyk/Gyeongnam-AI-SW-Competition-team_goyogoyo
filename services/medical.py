# 의료 데이터
"""
[미구현 - 자리만 잡아 둔 모듈] 의료기관 위치 기반 조회 서비스.

현재 상태:
    - 이 파일은 어디에서도 import되지 않는다.
    - 구별 의료기관 수(hospital_count)는 scripts/ingest_hira_hospital_data.py가 집계해
      data/region_indicators.csv에 저장하고, services/region_data.py로 조회한다
      (의원·치과의원·한의원·보건소 등 전 종별 합산 - "병원 수"로 단정하지 않는다).
    - 의료기관 좌표 데이터가 없어 위치 기반 주변 의료기관 검색은 지원하지 않는다.
    - emergency_hospital_count(응급실 운영 병원 수)는 "미확보"다.

예정 역할:
    - 의료기관 좌표를 확보하면 services/bus_stops.py·services/convenience.py와 같은 방식
      (services/geo.py의 직선거리 계산 재사용)으로 주변 의료기관 조회를 제공한다.
"""
