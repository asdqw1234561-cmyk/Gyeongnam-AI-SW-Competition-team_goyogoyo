# references/ — 개발 Agent 근거 자료함

여기에 넣은 자료를 `/steward`가 읽고 프로젝트 방향 판단에 사용한다. **원본은 수정하지 않는다**(`.claude/settings.json`에서 Edit/Write 차단).

| 폴더 | 넣을 자료 | 우선순위 |
|---|---|---|
| `competition/` | 공식 공고·운영규정·사업설명회·교육자료·제출 양식 | 1~3 (최상위 기준) |
| `project/` | 팀 기획서·기존 발표자료·아이디어 정리·참가 준비 자료 | 6 (현재 코드·데이터보다 낮음) |

- 지원 형식: PDF(이미지 슬라이드 포함), HWP, DOCX, PPTX, TXT, MD, CSV. 그 외는 Agent가 Read 도구로 직접 확인한다.
- 파일을 넣고 `/steward` 또는 "새 자료 넣었으니 확인해서 반영할 것 찾아봐"라고 하면 된다. 세션 시작 hook도 새 자료를 알려준다.
- 분석 결과: `docs/agent/REFERENCE_INDEX.md`(색인), `docs/agent/COMPETITION_REQUIREMENTS.md`(공식 요구사항).
- 원본 PDF·HWP의 git 커밋 여부는 아직 미정이다(`docs/agent/DECISIONS.md` OPEN-2).
