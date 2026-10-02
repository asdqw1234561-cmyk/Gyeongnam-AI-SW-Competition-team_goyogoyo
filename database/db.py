# DB 처리
"""
[미구현 - 자리만 잡아 둔 모듈] 데이터베이스 저장·조회.

현재 상태:
    - 이 파일은 어디에서도 import되지 않는다.
    - 모든 데이터는 CSV/GeoJSON 파일(data/)을 읽기 전용으로 사용한다
      (services/region_data.py의 _load_regions/_load_indicators 등).
    - database/app.db는 .gitignore에 등록돼 있으며 현재 코드에서 만들거나 쓰지 않는다.

예정 역할:
    - DB로 옮길 경우 services/region_data.py의 private 로더 내부만 교체하고, 공개 함수의
      시그니처와 반환 형식은 그대로 유지한다(그 모듈 docstring 참고).
"""
