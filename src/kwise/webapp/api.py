"""화면이 부르는 파이썬 쪽 — pywebview ``js_api`` 로 건넨다.

**돌려주는 것은 JSON 으로 바뀌는 값뿐이다** (사전 · 목록 · 글 · 수). 그래프는 Altair 차트를
``to_dict()`` 로 넘기고 화면이 vega-embed 로 그린다 — NiceGUI 판과 같은 그림이다.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from kwise import __version__

#: 자가 점검 — 이 환경 변수에 경로를 주면 화면이 다 선 뒤 그 상태를 거기에 적는다 (시험용).
SELFTEST_ENV = "KWISE_SELFTEST"


def _jsonable(value: Any) -> Any:
    """Altair 가 남긴 날짜 따위를 글로 — 다리가 JSON 으로 못 바꾸면 화면에 아무것도 안 온다."""
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


class Api:
    """창 하나의 다리. 메서드 이름이 곧 화면이 부르는 이름이다 (``pywebview.api.<이름>``)."""

    def ready(self, info: dict[str, Any]) -> None:
        """화면이 다 섰다는 알림 — 시험할 때만 그 상태를 파일로 남긴다(평소에는 아무것도 안 한다)."""
        target = os.environ.get(SELFTEST_ENV)
        if target:
            Path(target).write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")

    def ping(self) -> dict[str, Any]:
        return {"version": __version__, "message": "웹서버 없이 파이썬과 이어졌습니다."}

    def sample_chart(self) -> dict[str, Any]:
        """시제품 확인용 — 공용 차트 하나를 Vega 사양으로."""
        import altair as alt
        import pandas as pd

        frame = pd.DataFrame({"월": ["1월", "2월", "3월"], "kW": [120, 180, 150]})
        chart = alt.Chart(frame).mark_bar().encode(x="월", y="kW")
        return _jsonable(chart.to_dict())
