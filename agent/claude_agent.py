# Claude API 연동
"""
[미구현 - 자리만 잡아 둔 모듈] Anthropic API(anthropic SDK)로 Claude를 직접 호출하는 연동부.

현재 상태:
    - 이 파일은 어디에서도 import되지 않는다.
    - Claude는 지금 agent/location_agent.py가 Claude Code CLI(`claude -p`)를 subprocess로
      호출하는 방식으로만 쓰인다(실행 PC에 CLI 설치·로그인 필요).

예정 역할:
    - 배포·다중 사용자 환경에서 CLI 대신 API 키로 Claude를 호출(tool use).
    - 구현 시에도 CLAUDE.md 원칙을 그대로 따른다: 좌표·반경·가중치·시설 수는 Python이
      소유하고, AI 출력은 검증 후에만 사용한다.
"""
