r"""화면 표를 내는 **한 자리** (S161 2절).

`st.dataframe` 을 열세 자리가 저마다 부르고 있었고 **아무도 표기를 안 지났다** —
월별 명세가 `118936.77419354838` · `0.0006944444444444445` 을 그대로 냈고,
DR 감축 여력 표가 `30.929400469628767` 을 냈고, 3단계 계산 근거 표가
`**기본요금 기반이 달라집니다.**` 를 표식째 냈다. **표 칸은 마크다운을 안
그린다** — 화면에서 별표가 굵게 되는 것은 `st.markdown` 쪽 이야기다.

Excel 이 지나는 문(:func:`kwise.report.columns.display_frame`)을 화면도 지난다 —
**같은 자료가 두 꼴로 나오는 것을 막는 것이 이 자리의 값이다.** 자릿수를 열마다
손으로 적지 않는 까닭도 같다: 표가 늘 때마다 빠뜨리고 그 빠뜨림이 조용하다.

    from kwise.ui import tables
    tables.show(frame, hide_index=True, width="stretch")

**이름이 `table` 이 아닌 까닭** — `measures.py`·`compare.py` 가 요금표를
`table` 이라는 지역 이름으로 들고 있어 그 이름을 들여오면 **요금표를 가린다**
(mypy 가 「TariffTable not callable」 로 네 자리를 짚었다).

**`st.dataframe` 을 이 모듈 밖에서 부르지 않는다** — `tests\test_ui.py` 의
`test_화면_표는_한_자리를_지난다` 가 문다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal, cast

import pandas as pd
import streamlit as st

from kwise.report.columns import display_frame

__all__ = ["TableData", "show"]

#: 표에 넘길 수 있는 것. **`st.dataframe` 이 받던 것을 그대로 받는다** — 좁히면
#: 부르는 자리가 이 문을 피해 갈 까닭이 생긴다.
type TableData = pd.DataFrame | Mapping[str, Sequence[object]] | Sequence[Mapping[str, object]]


def show(
    data: TableData,
    *,
    hide_index: bool | None = None,
    width: Literal["stretch", "content"] | int = "stretch",
    column_config: Mapping[str, object] | None = None,
) -> None:
    """표 하나. **낼 때만 접는다** — 넘어온 프레임은 그대로 둔다."""
    frame = data if isinstance(data, pd.DataFrame) else pd.DataFrame(data)
    st.dataframe(
        display_frame(frame),
        hide_index=hide_index,
        width=width,
        # **남의 형을 좁히지 않는다** (`CLAUDE.md` 코드 규약). 열 설정의 값은
        # streamlit 안쪽 형이라 이름으로 적으면 그쪽 판이 바뀔 때마다 깨진다.
        column_config=cast(Any, column_config),
    )
