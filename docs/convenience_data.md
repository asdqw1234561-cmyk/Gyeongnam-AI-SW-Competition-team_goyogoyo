# 창원시 생활편의시설(편의점) 데이터

담당: 황승구 · 브랜치: `hwang`

## 1. 수집 범위

| 항목 | 상태 |
|---|---|
| 편의점 (창원시 5개 구) | 수집 스크립트 구현 완료. 실제 수치는 아래 실행 후 CSV로 확정 |
| 대형마트 | **미확보** (이번 단계 범위 아님, 집계 CSV에 `미확보`로 표시) |

대상 구와 시군구코드(행정표준코드):

| region_id | 구 | 시군구코드 |
|---|---|---|
| CW-UICHANG | 의창구 | 48121 |
| CW-SEONGSAN | 성산구 | 48123 |
| CW-MASANHAPPO | 마산합포구 | 48125 |
| CW-MASANHOEWON | 마산회원구 | 48127 |
| CW-JINHAE | 진해구 | 48129 |

## 2. 데이터 출처

- **소상공인시장진흥공단_상가(상권)정보_API** — 공공데이터포털 <https://www.data.go.kr/data/15012005/openapi.do>
  - 오퍼레이션: `storeListInDong` (`divId=signguCd`, `key=시군구코드`)
  - 서비스 URL: `https://apis.data.go.kr/B553077/api/open/sdsc2`
  - 편의점 판별: 응답의 상권업종 소분류명 `indsSclsNm == "편의점"`
  - 기준일: 응답 header의 `stdrYm`(기준년월). 응답에 없으면 `미확보`로 기록
- 업종분류 참고: 소상공인시장진흥공단_상가(상권)정보 업종코드 <https://www.data.go.kr/data/15067631/fileData.do> (2023년 업종분류 개편 반영)

> 상가정보는 사업자·카드 데이터를 기반으로 분기마다 갱신되는 자료라 폐업이 늦게 반영되거나 실제 영업 현황과 다를 수 있습니다. "기준년월 시점의 등록 업소 수"로 해석해 주세요.

## 3. 실행 방법 (Windows PowerShell)

```powershell
# 0) 패키지 설치 (최초 1회)
pip install -r requirements.txt

# 1) 인증키 준비
#    https://www.data.go.kr/data/15012005/openapi.do 에서 "활용신청"
#    마이페이지 > 데이터활용 > Open API에서 "일반 인증키(Decoding)" 복사
#    프로젝트 루트 .env 파일에 추가 (.env는 git에 올라가지 않음)
#      SBIZ_SERVICE_KEY=발급받은_인증키

# 2) 응답 구조 확인 (파일 생성 안 함)
python scripts/collect_convenience_stores.py inspect

# 3) 수집 + 구별 집계 미리보기 (dry-run, 파일 생성 안 함)
python scripts/collect_convenience_stores.py collect

# 4) CSV 저장
python scripts/collect_convenience_stores.py collect --save

# (선택) 편의점 업종코드를 찾아서 서버 필터로 빠르게 수집
python scripts/collect_convenience_stores.py codes --keyword 편의점
python scripts/collect_convenience_stores.py collect --save --inds-scls-cd <찾은코드>
```

기본 모드는 업종코드 없이 구 전체 상가를 받아서 이름으로 거르기 때문에 요청 수가 많아요(구당 수십 건, numOfRows=1000 기준). 개발계정 일일 트래픽 안에서 충분히 돌아가지만, `--inds-scls-cd`를 쓰면 몇 건으로 끝나요.

## 4. 결과 파일

`data/convenience/` 폴더에 저장됩니다. 엑셀에서 열어도 한글이 깨지지 않도록 UTF-8 BOM으로 저장해요.

**changwon_convenience_stores.csv** — 업소 단위 목록

| 컬럼 | 의미 |
|---|---|
| region_id, district | 구 |
| facility_name, branch_name | 시설명(상호), 지점명 |
| facility_type | 시설 유형 (`편의점`) |
| road_address, jibun_address, adong_name | 소재지 (도로명/지번/행정동) |
| lon, lat | 좌표 |
| bizes_id | 상가업소번호 (중복 제거 기준) |
| source | 출처 |
| reference_ym | 기준년월 (API `stdrYm`) |
| collected_date | 수집일 |

**changwon_convenience_counts.csv** — 구별 집계

`region_id, district, facility_type, count, unit, source, reference_ym, collected_date, data_status, note`

- `data_status`: `확보` / `미확보`. API 호출에 실패한 구는 count를 비우고 `미확보`로 남깁니다.
- 대형마트 5개 행은 `미확보`로 들어가 있어요.

## 5. 코드에서 조회하기 (`services/convenience.py`)

```python
from services import convenience

convenience.get_district_counts()            # 5개 구 편의점 수 (항상 5행, 미수집이면 미확보)
convenience.get_district_counts("대형마트")  # 현재 전부 미확보
convenience.get_district_count("창원시 성산구")
convenience.get_stores_by_district("의창구", keyword="GS25", limit=20)
```

반환 dict는 `services/region_data.py`의 지표 형식(`value, unit, source, reference_date, data_status, note`)과 같고,
`indicator_code`(`convenience_store_count` / `mart_count`)도 들어 있어 나중에 `data/region_indicators.csv`에 반영할 때 그대로 쓸 수 있어요. 이번 작업에서 `region_indicators.csv`는 수정하지 않았어요.

간단 확인: `python -m services.convenience`

## 6. 테스트

```powershell
python -m unittest tests.test_convenience -v
```

가짜 응답(mock)으로 페이지 순회, 편의점 필터, 중복 제거, 구 이름 불일치 제외, API 실패 시 미확보 처리, CSV → 조회 흐름을 확인합니다. 실제 API는 호출하지 않아요.

## 7. 확인되지 않은 점

- 편의점의 업종 소분류코드는 공식 문서로 확인하지 못했어요. 그래서 기본은 이름(`indsSclsNm`) 일치 방식이고, 코드는 `codes` 명령으로 실제 응답에서 확인합니다.
- 응답 필드명(`bizesNm`, `rdnmAdr`, `stdrYm` 등)은 공개 명세 기준이에요. 첫 실행 때 `inspect`로 한번 확인해 주세요.
