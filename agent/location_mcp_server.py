# 위치 기반 주변 시설 조회 MCP 서버 - claude -p 반복형 Agent용 도구 제공
"""
agent/location_agent.py의 run_location_agent()가 claude CLI(`claude -p`)를
--mcp-config로 띄울 때 함께 실행되는 stdio MCP 서버다. Claude는 이 서버의
도구를 직접 반복 호출하며(결과를 보고 다음 도구를 고름) 최종 답변을 만든다.

[검색 중심 좌표·반경은 여전히 Python이 통제]
    도구 인자에는 lat/lon/radius_m이 아예 없다. 좌표·반경·표시 개수 기본값·
    사용자 요청 제약조건은 run_location_agent()가 환경변수로 이 프로세스에
    넘기며, 도구는 항상 그 값으로만 조회한다.

[guardrail - LocationToolbox]
    - 사용자가 요청하지 않은 시설 도구, 반경 비교를 요청하지 않았는데 반경별
      비교 도구를 부르면 실행하지 않고 "올바른 도구"를 안내하는 오류를 돌려준다
      (AI가 그 안내를 보고 다시 고르게 하는 것이 반복형 Agent의 장점).
    - "가장 가까운" 요청이면 max_results를 1로 강제한다.
    - 전체 도구 호출 수는 MAX_AGENT_TOOL_CALLS로 제한한다.
    모든 호출(차단 포함)은 LOCATION_MCP_LOG(JSONL)에 기록되며, run_location_
    agent()는 이 로그로 "실제 실행한 도구"를 화면에 보여준다.

    python agent/location_mcp_server.py   # (직접 실행할 일은 없음 - claude CLI가 띄운다)
"""

from __future__ import annotations

import json
import os
import sys

# claude CLI가 임시 디렉터리에서 이 스크립트를 띄우므로 프로젝트 루트를 경로에 추가한다.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent import location_agent as la  # noqa: E402

SERVER_NAME = "location"
MAX_AGENT_TOOL_CALLS = 6

ENV_LAT = "LOCATION_MCP_LAT"
ENV_LON = "LOCATION_MCP_LON"
ENV_RADIUS_M = "LOCATION_MCP_RADIUS_M"
ENV_MAX_RESULTS = "LOCATION_MCP_MAX_RESULTS"
ENV_CONSTRAINTS = "LOCATION_MCP_CONSTRAINTS"
ENV_LOG = "LOCATION_MCP_LOG"


class LocationToolbox:
    """도구 실행 + guardrail. MCP와 무관한 순수 Python이라 단위 테스트가 쉽다."""

    def __init__(
        self,
        lat: float,
        lon: float,
        radius_m: int,
        ui_max_results: int,
        constraints: dict,
        log_path: str | None = None,
    ):
        self.lat = lat
        self.lon = lon
        self.radius_m = radius_m
        self.ui_max_results = ui_max_results
        self.constraints = constraints or {}
        self.log_path = log_path
        self.call_count = 0

    @classmethod
    def from_env(cls) -> "LocationToolbox":
        return cls(
            lat=float(os.environ[ENV_LAT]),
            lon=float(os.environ[ENV_LON]),
            radius_m=int(os.environ[ENV_RADIUS_M]),
            ui_max_results=int(os.environ[ENV_MAX_RESULTS]),
            constraints=json.loads(os.environ.get(ENV_CONSTRAINTS) or "{}"),
            log_path=os.environ.get(ENV_LOG),
        )

    # -- guardrail ---------------------------------------------------------
    def _scope_violation(self, tool: str) -> str | None:
        facilities = self.constraints.get("requested_facilities", [])
        wants_comparison = self.constraints.get("wants_radius_comparison", False)
        if not facilities:
            return None
        if tool == "compare_nearby_facilities" and not wants_comparison:
            allowed = []
            if "bus_stop" in facilities:
                allowed.append("find_nearby_bus_stops")
            if "convenience" in facilities:
                allowed.append("find_nearby_convenience_stores")
            return (
                "사용자는 반경별 비교를 요청하지 않았습니다. "
                f"대신 {', '.join(allowed)} 도구를 사용하세요."
            )
        if tool == "find_nearby_bus_stops" and "bus_stop" not in facilities:
            return "사용자는 버스정류장을 요청하지 않았습니다. find_nearby_convenience_stores를 사용하세요."
        if tool == "find_nearby_convenience_stores" and "convenience" not in facilities:
            return "사용자는 편의점을 요청하지 않았습니다. find_nearby_bus_stops를 사용하세요."
        return None

    def _resolve_max_results(self, proposed: object) -> tuple[int, str | None]:
        if self.constraints.get("nearest_only"):
            note = None if proposed == 1 else "'가장 가까운' 요청이라 표시 개수를 1로 보정했습니다."
            return 1, note
        if isinstance(proposed, (int, float)) and not isinstance(proposed, bool):
            return max(1, min(int(proposed), la.MAX_RESULTS_LIMIT)), None
        return self.ui_max_results, None

    # -- 실행 -------------------------------------------------------------
    def call(self, tool: str, reason: str = "", max_results: object = None) -> dict:
        """도구 하나를 실행(또는 차단)하고 AI에게 돌려줄 payload를 반환한다."""
        self.call_count += 1
        entry: dict = {"tool": tool, "reason": str(reason or "").strip()[:200]}

        if self.call_count > MAX_AGENT_TOOL_CALLS:
            entry.update(executed=False, blocked=True,
                         error=f"도구 호출 한도({MAX_AGENT_TOOL_CALLS}회)를 넘었습니다.")
        elif (violation := self._scope_violation(tool)) is not None:
            entry.update(executed=False, blocked=True, error=violation)
        else:
            entry["blocked"] = False
            try:
                if tool == "compare_nearby_facilities":
                    entry["result"] = la.tool_compare_nearby_facilities(self.lat, self.lon)
                else:
                    mr, note = self._resolve_max_results(max_results)
                    entry["radius_m"] = self.radius_m
                    entry["max_results"] = mr
                    if note:
                        entry["note"] = note
                    fn = (la.tool_find_nearby_bus_stops if tool == "find_nearby_bus_stops"
                          else la.tool_find_nearby_convenience_stores)
                    entry["result"] = fn(self.lat, self.lon, self.radius_m, mr)
                entry.update(executed=True, error=None)
            except Exception as exc:  # 서비스 함수 오류도 AI에게 그대로 알려준다
                entry.update(executed=False, error=str(exc))

        self._log(entry)
        if entry.get("executed"):
            payload = {"status": "ok", "result": entry["result"]}
            if entry.get("radius_m") is not None:
                payload["applied_radius_m"] = entry["radius_m"]
            if entry.get("note"):
                payload["note"] = entry["note"]
            return payload
        return {"status": "error", "error": entry["error"]}

    def _log(self, entry: dict) -> None:
        if not self.log_path:
            return
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")


def _dump(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def main() -> None:
    from mcp.server.mcpserver import MCPServer

    box = LocationToolbox.from_env()
    server = MCPServer(SERVER_NAME)

    @server.tool()
    def find_nearby_bus_stops(reason: str, max_results: int | None = None) -> str:
        """사용자가 지도에서 확정한 위치 주변의 버스정류장을 가까운 순으로 조회한다.
        반경은 이미 결정되어 있다. max_results는 표시 개수(선택, 1~30)이며
        '가장 가까운 곳 하나'면 1로 준다. reason에는 호출 이유를 한 문장으로 적는다."""
        return _dump(box.call("find_nearby_bus_stops", reason, max_results))

    @server.tool()
    def find_nearby_convenience_stores(reason: str, max_results: int | None = None) -> str:
        """사용자가 지도에서 확정한 위치 주변의 편의점을 가까운 순으로 조회한다.
        반경은 이미 결정되어 있다. max_results는 표시 개수(선택, 1~30)이며
        '가장 가까운 곳 하나'면 1로 준다. reason에는 호출 이유를 한 문장으로 적는다."""
        return _dump(box.call("find_nearby_convenience_stores", reason, max_results))

    @server.tool()
    def compare_nearby_facilities(reason: str) -> str:
        """확정된 위치 기준 300m/500m/1km 반경별 버스정류장·편의점 전체 건수를 한 번에
        비교한다. 사용자가 여러 반경을 비교하고 싶어할 때만 쓴다."""
        return _dump(box.call("compare_nearby_facilities", reason))

    server.run("stdio")


if __name__ == "__main__":
    main()
