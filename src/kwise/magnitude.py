r"""크기 표기 — **0 으로 뭉개면 문구가 거짓말이 된다** (31세션 0-1).

    주간 지상역률 92.0% — 기준 92% 대비 0.0%p 미달, 기본요금의 +0.0% 추가.

역률 92.0% 에서 나오던 문구다. 두 군데가 사실과 어긋났다 — 기준과 **같으면
미달이 아니고**, `0.0%p` 는 미달폭이 아니라 반올림 결과다. 91.96% 에서도 똑같은
문구가 나왔는데 그쪽은 **진짜 미달인데 폭이 안 보였다.**

원인이 둘이라 고치는 법도 둘이다.

    경계     ``<=`` 로 같은 값을 미달 쪽에 넣었다 → 세 갈래로 가른다 (충족·미달·초과)
    반올림   0 이 아닌 값이 ``0.0`` 으로 찍힌다 → **보일 때까지 자릿수를 늘린다**

이 모듈이 뒤쪽을 맡는다. 앞쪽은 문구를 만드는 자리가 직접 가른다 — 어느 갈래가
어떤 말이 되는지는 그 자리의 제도 지식이라 여기로 끌고 올 것이 아니다.

**금액은 :mod:`kwise.money` 가 맡는다.** 그쪽은 반대로 **일부러 굵게 절사**한다
(천 원 단위). 크기가 곧 뜻인 자리와 금액이 뜻인 자리는 규칙이 다르므로 두 모듈을
합치지 않는다.

    magnitude(0.04, "%p")        →  '0.04%p'
    magnitude(0.5, "%p")         →  '0.5%p'
    magnitude(0.0, "%p")         →  '0.0%p'   (진짜 0 은 늘리지 않는다)

**pandas 도 Streamlit 도 import 하지 않는다.** 계산 모듈이 부르는 자리다.
"""

from __future__ import annotations

__all__ = ["DEFAULT_DECIMALS", "MAX_DECIMALS", "magnitude", "places"]

DEFAULT_DECIMALS = 1
"""보통 자릿수. 이 자리에서 0 이 아니면 그대로 쓴다."""

MAX_DECIMALS = 4
"""여기까지만 늘린다. 더 잘게 적어도 읽는 사람이 쓸 수 없는 크기다."""


def places(
    value: float, *, decimals: int = DEFAULT_DECIMALS, max_decimals: int = MAX_DECIMALS
) -> int:
    """이 값이 **0 으로 보이지 않는** 최소 자릿수.

    값이 정확히 0 이면 늘리지 않는다 — 0 은 0 으로 보이는 것이 맞다.
    :data:`MAX_DECIMALS` 까지 늘려도 0 이면 거기서 멈춘다.
    """
    if value == 0.0:
        return decimals
    while decimals < max_decimals and round(value, decimals) == 0.0:
        decimals += 1
    return decimals


def magnitude(
    value: float,
    unit: str = "",
    *,
    decimals: int = DEFAULT_DECIMALS,
    max_decimals: int = MAX_DECIMALS,
    sign: bool = False,
) -> str:
    """크기를 **0 으로 뭉개지 않고** 적는다.

    Args:
        unit: 숫자 뒤에 붙일 단위. ``"%p"`` · ``"%"`` · ``" kW"``.
        sign: 양수에도 ``+`` 를 붙일지. 방향을 말로 적는 문구에서는 끈다 —
            「+0.1% 추가」 는 같은 말을 두 번 한다.
    """
    used = places(value, decimals=decimals, max_decimals=max_decimals)
    return f"{value:{'+' if sign else ''},.{used}f}{unit}"
