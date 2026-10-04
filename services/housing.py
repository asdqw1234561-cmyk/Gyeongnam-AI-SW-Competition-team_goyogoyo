# 주거비 데이터
"""
[미구현 - 자리만 잡아 둔 모듈] 주거비(월세·전세) 데이터 조회 서비스.

현재 상태:
    - 이 파일은 어디에서도 import되지 않는다.
    - data/region_indicators.csv의 monthly_rent_avg(월세 평균)·jeonse_avg(전세 평균)는
      22개 지역 모두 "미확보"이며, 점수 계산(analysis/scoring.py)에 쓰지 않는다.
    - app.py의 "주거비 예산" 입력은 참고용으로만 저장된다.

예정 역할:
    - 실제 공공데이터(출처 미정)를 확보하면 services/region_data.py와 같은 지표 형식
      (value / source / reference_date / data_status / note)으로 제공한다.
    - 데이터가 없으면 값을 추정하지 않고 "미확보"로 둔다.
"""
