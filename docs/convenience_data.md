# 창원시 생활편의시설(편의점) 데이터

담당: 황승구 · 브랜치: `hwang`

## 1. 수집 범위와 결과

| 항목 | 상태 |
|---|---|
| 편의점 (창원시 5개 구) | **확보** — 950개, 기준년월 2026-06, 수집일 2026-10-01 |
| 대형마트 | **미확보** (이번 단계 범위 아님, 집계 CSV에 `미확보`로 표시) |

### 편의점 수집 결과 (2026-10-01 수집, 기준년월 202606)

| 구 | 편의점 수 | 조회한 전체 상가 레코드 |
|---|---:|---:|
| 의창구 | 199 | 11,213 |
| 성산구 | 245 | 13,614 |
| 마산합포구 | 159 | 10,078 |
| 마산회원구 | 151 | 8,241 |
| 진해구 | 196 | 8,450 |
| **합계** | **950** | 51,596 |

수집 후 점검 결과:
- 950건 모두 상권업종 소분류가 `편의점`, 상가업소번호 중복 0건
- 950건 모두 도로명주소에 해당 구 이름이 포함됨 (구 오매핑 0건)
- 950건 모두 좌표(위도 35.07~35.35, 경도 128.40~128.82)가 있음
- 상호 기준 브랜드 분포: CU 334, GS25 291, 세븐일레븐 170, 이마트24 86, 미니스톱 8, 기타 61

> **주의: 같은 주소 중복 등록 가능성** — 같은 도로명주소에 같은 브랜드가 2건 이상 있는 경우가 59곳(128행, 1곳당 1건을 남기면 69건 초과)입니다.
> `세븐일레븐마산선진빌딩점` / `세븐마산선진빌딩점 코리아`처럼 같은 매장이 다른 이름으로 두 번 등록된 것으로 보이는 경우도 있고,
> 경남대학교 캠퍼스처럼 같은 주소에 실제로 매장이 여러 개인 경우도 있어 **자동으로 지우지 않았습니다.**
> 따라서 950은 "상가정보에 등록된 편의점 업소 수"이고, 실제 영업 매장 수는 이보다 조금 적을 수 있습니다.

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

# 4) CSV 저장 (한 구라도 수집이 불완전하면 저장하지 않고 종료)
python scripts/collect_convenience_stores.py collect --save

# (선택) 편의점 업종코드를 찾아서 서버 필터로 빠르게 수집
python scripts/collect_convenience_stores.py codes --keyword 편의점
python scripts/collect_convenience_stores.py collect --save --inds-scls-cd <찾은코드>
```

기본 모드는 업종코드 없이 구 전체 상가를 받아서 이름으로 거르기 때문에 요청 수가 많아요(구당 수십 건, numOfRows=1000 기준). 개발계정 일일 트래픽 안에서 충분히 돌아가지만, `--inds-scls-cd`를 쓰면 몇 건으로 끝나요.

### 수집 완전성 검증

구마다 아래 조건을 **모두** 만족해야 집계가 `확보`로 저장됩니다.

- 모든 페이지 호출 성공 (실패 시 페이지당 `--retries`회, 기본 2회 재시도)
- 응답에 `totalCount`가 있고, 조회 도중 값이 바뀌지 않음
- `--max-pages` 안에 마지막 페이지까지 도달
- 실제로 받은 레코드 수가 `totalCount`와 같음

하나라도 어긋나면 그 구는 `count`를 비우고 `data_status=미확보`로 표시하며, 일부만 받은 업소는 목록에 넣지 않습니다.
`--save` 실행 중 불완전한 구가 있으면 **기존 CSV를 덮어쓰지 않고** 종료 코드 2로 끝납니다.
그래도 미확보 표시로 저장하려면 `--allow-partial`을 붙이세요.

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

### 주변 편의점 조회 (직선거리)

```python
from services import convenience

result = convenience.find_nearby_stores(lat=35.2280, lon=128.6811, radius_m=500, max_results=5)
result["total_count"]       # 반경 안 전체 편의점 수
result["displayed_count"]   # 화면 표시 목록 개수 (max_results 이하)
result["stores"]            # 직선거리 가까운 순
# [{"rank": 1, "facility_name": ..., "branch_name": ..., "district": ...,
#   "road_address": ..., "jibun_address": ..., "lat": ..., "lon": ...,
#   "straight_distance_m": 154}, ...]

convenience.count_nearby_by_radius(35.2280, 128.6811)
# {"counts": {300: 12, 500: 21, 1000: 91}, "distance_type": "직선거리", ...}
```

- **입력**: 위도, 경도(WGS84), 검색 반경(m, 300/500/1000 권장, 1~5000 허용), 최대 표시 개수(1~100). 숫자 문자열(`"500"`)도 받습니다.
- **거리**: 하버사인 공식으로 계산한 두 좌표 사이의 **직선거리**(m, 반올림)입니다. 실제 도보거리나 대중교통 이동시간이 아니므로,
  화면에도 반드시 "직선거리"로 표기하세요. 결과에 `distance_type="직선거리"`와 `distance_note` 안내 문구가 함께 들어 있습니다.
- **정렬**: 직선거리 가까운 순 (거리가 같으면 상호명 순)
- **오류 처리** (예외를 던지지 않음):
  - 위경도가 숫자가 아니거나 범위를 벗어남, 반경·개수가 범위 밖 → `status="invalid_input"`, 빈 목록, 안내 `message`
  - CSV가 없거나 좌표 컬럼이 없음 → `status="no_data"`
  - 좌표가 비었거나 잘못된 업소 행 → 계산에서 빼고 `warnings`에 건수 표시
  - 입력 위치가 창원시 범위를 벗어남 → 계산은 하되 `warnings`에 안내
- CSV는 파일이 바뀔 때만 다시 읽어서(수정 시각 기준 캐시) Streamlit에서 반복 호출해도 가볍습니다.

실데이터 확인 예시 (창원시청 부근 근사 좌표 35.2280, 128.6811): 300m 12개, 500m 21개, 1km 91개.
이 중 일부는 위에서 설명한 같은 주소 중복 등록일 수 있습니다.

## 6. 테스트

```powershell
python -m unittest tests.test_convenience -v
```

가짜 응답(mock)과 임시 CSV로 23개 항목을 확인합니다. 실제 API는 호출하지 않아요.

- 수집: 페이지 순회, 편의점 필터, 중복 제거, 구 이름 불일치 제외
- 완전성: 중간 페이지 실패, 재시도 후 성공, max_pages 도달, 빈 페이지, totalCount 변경·누락, 불완전 시 저장 거부(기존 CSV 보존)
- 주변 조회: 300m·500m·1km 개수, 직선거리 정렬과 값, 전체 수 vs 표시 목록, 잘못된 입력 14종, 반경 내 0건, 창원 밖 위치, 데이터 파일 없음, 좌표 컬럼 없음, 파일 변경 시 캐시 갱신

## 7. 확인된 점 / 확인되지 않은 점

- 편의점의 업종 소분류코드는 공식 문서로 확인하지 못했어요. 그래서 기본은 이름(`indsSclsNm`) 일치 방식이고, 코드는 `codes` 명령으로 실제 응답에서 확인합니다.
- 응답 필드명(`bizesNm`, `rdnmAdr`, `lat`, `lon`, `stdrYm` 등)은 2026-10-01 실제 수집으로 동작을 확인했어요.
- 같은 주소 중복 등록이 실제 중복인지 별도 매장인지는 확인하지 못했어요(1절 주의 참고).
- 주변 조회 거리는 직선거리이며, 도보 경로나 이동시간은 계산하지 않았어요.
