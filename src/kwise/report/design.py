"""PPT 디자인 가이드 — **색·크기가 오는 한 곳** (36세션 3절).

슬라이드(:mod:`kwise.report.slides`)와 보고서 png(:mod:`kwise.report.figures`)가
같은 팔레트를 쓴다. 값은 ``data\\ppt_design.json`` 에 있고 **코드에 두지 않는다** —
기준 데이터(``rules_kr.json``)를 코드 기본값 없이 파일에서만 읽는 것과 같은
이유다. 가이드가 바뀌면 고칠 자리가 한 곳이어야 한다.

    뼈대     16:9 · 13.333in × 7.5in · 여백 0.5in
    글꼴     후보를 늘어놓고 **이름만** 적는다 (pptx 는 읽는 쪽이 그린다)
    색       딥그린·다크는 전체 배경 밴드, 코랄·블루는 작은 포인트
    차트     png 전용 팔레트. 흰 캔버스가 확정이라 화면 규약과 다르다

**글꼴 이름을 코드에 박지 않는다.** OS 전용 이름을 ``src\\`` 안에 두면 배포
시험(``tests\\test_deployment.py``)이 잡는다 — 그 시험이 막으려는 것이 바로
"그 OS 밖에서 깨지는 이름"이고, 설정 파일은 고쳐 끼울 수 있으므로 자리가 다르다.
후보가 하나도 없으면 :attr:`Typography.fallback` 로 물러선다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from kwise.rules.store import data_dir

__all__ = [
    "DESIGN_FILENAME",
    "ChartPalette",
    "DesignGuide",
    "DesignGuideError",
    "Palette",
    "SlideGeometry",
    "TypeScale",
    "Typography",
    "design_path",
    "load_design_guide",
]

DESIGN_FILENAME = "ppt_design.json"


class DesignGuideError(RuntimeError):
    """가이드 파일이 없거나 항목이 빠졌다. **조용히 기본값으로 넘어가지 않는다.**"""


def design_path(root: Path | None = None) -> Path:
    return data_dir(root) / DESIGN_FILENAME


# ===================================================================== 뼈대


@dataclass(frozen=True)
class SlideGeometry:
    """슬라이드 뼈대 (36세션 3-1). 단위는 인치다.

    Attributes:
        text_slack: 텍스트 상자에 둘 **여유 비율.** 렌더러마다 글자 폭이 달라
            딱 맞춘 상자는 다른 PowerPoint 에서 넘친다.
    """

    width_in: float
    height_in: float
    margin_in: float
    block_gap_in: float
    title_gap_in: float
    text_slack: float
    rule_pt: float

    @property
    def content_width_in(self) -> float:
        """좌우 여백을 뺀 폭."""
        return self.width_in - 2 * self.margin_in

    def slack(self, inches: float) -> float:
        """텍스트 상자 높이에 여유를 얹는다 (36세션 3-1)."""
        return inches * (1.0 + self.text_slack)


# ===================================================================== 글꼴


@dataclass(frozen=True)
class Typography:
    """글꼴 후보와 물러설 곳.

    pptx 에는 **이름만** 적는다 — Word 와 같다. 서버에 그 글꼴이 깔려 있을
    필요가 없고, 없으면 읽는 쪽 PowerPoint 가 대체한다.
    """

    candidates: tuple[str, ...]
    fallback: str

    @property
    def primary(self) -> str:
        """문서에 적을 이름. 후보가 비면 물러선 이름이다."""
        return self.candidates[0] if self.candidates else self.fallback


@dataclass(frozen=True)
class TypeScale:
    """크기 위계 (36세션 3-3). 단위는 pt. **슬라이드당 큰 타이틀은 하나다.**"""

    cover: float
    section: float
    slide_title: float
    card_title: float
    body: float
    caption: float


# ===================================================================== 색


@dataclass(frozen=True)
class Palette:
    """슬라이드 색 (36세션 3-2).

    딥그린·다크는 **전체 배경 밴드로만** 쓰고, 코랄·블루는 라벨 같은 작은
    포인트로만 쓴다. 슬라이드 폭을 가로지르는 색 바는 만들지 않는다 (3-5).

    **읽지 않는 값을 두지 않는다.** 고쳐도 아무 일이 일어나지 않는 항목은
    가이드를 못 믿게 만든다 — 이 파일이 막으려는 바로 그것이다. 다크가
    둘(딥그린·다크 프라이머리)이라 **이름으로 고르게** 두었다 — 쓰지 않는 색을
    이름만 남겨 두는 대신이다.

    Attributes:
        cover_background: 표지 배경으로 쓸 색의 **이름.** ``deep_green`` 또는
            ``dark_primary``. 가이드에서 바꾸면 코드를 고치지 않아도 따라온다.
        closing_background: 마무리 배경. 샌드위치의 아랫빵이다 (37세션).
        on_dark_rule: 다크 배경 위의 구분선. 밝은 :attr:`rule` 을 그대로 쓰면
            검은 바탕에서 선이 튄다.
    """

    cover_background: str
    closing_background: str
    deep_green: str
    dark_primary: str
    white: str
    coral: str
    blue: str
    ink: str
    muted: str
    rule: str
    on_dark: str
    on_dark_muted: str
    on_dark_rule: str

    def _band(self, name: str, where: str) -> str:
        """전체 배경 밴드로 쓸 색 하나. **다크 둘 중에서만 고른다** (3-2).

        Raises:
            DesignGuideError: 코랄처럼 작은 포인트로만 쓰는 색을 골랐을 때.
        """
        allowed = {"deep_green": self.deep_green, "dark_primary": self.dark_primary}
        if name not in allowed:
            raise DesignGuideError(
                f"{where} 배경으로 쓸 수 없는 색입니다: {name}. "
                f"{' 또는 '.join(allowed)} 가운데 고르십시오 — 딥그린·다크만 "
                "전체 배경 밴드로 쓸 수 있습니다."
            )
        return allowed[name]

    @property
    def cover(self) -> str:
        """표지 배경색. 가이드가 이름으로 고른다."""
        return self._band(self.cover_background, "표지")

    @property
    def closing(self) -> str:
        """마무리 배경색 (37세션). 표지와 함께 샌드위치의 바깥을 이룬다."""
        return self._band(self.closing_background, "마무리")


@dataclass(frozen=True)
class ChartPalette:
    """png 전용 팔레트 (36세션 4절).

    **화면(altair)과 조건이 다르다.** 화면은 다크 모드를 타야 해서 글자에
    중립색(흰·회·검)을 박지 않는다는 규약이 있지만(35세션), png 는 흰 캔버스가
    확정이라 그 규약이 성립하지 않는다 — 오히려 어두운 중립색이라야 읽힌다.
    대신 **배경을 투명으로 굽지 않는다**: 배경이 확정이어야 이 규약이 선다.

    Attributes:
        series: 계열 색. 짙은 파랑이 첫 계열이다.
        band: 계시 시간대 색. **단가가 높을수록 짙다** — 화면과 같은 규칙이다.
        saving: 줄어드는 쪽(좋은 쪽). ``increase`` 는 늘어나는 쪽이다.
    """

    canvas: str
    text: str
    grid: str
    series: tuple[str, ...]
    day_series: tuple[str, ...]
    band: dict[str, str]
    base_fee: str
    saving: str
    increase: str
    neutral: str
    highlight: str
    fill: str
    fill_alpha: float


@dataclass(frozen=True)
class DesignGuide:
    """가이드 한 벌."""

    slide: SlideGeometry
    typography: Typography
    type_scale: TypeScale
    colors: Palette
    chart: ChartPalette
    updated: str = ""


# ===================================================================== 읽기


def _section(payload: dict[str, Any], key: str, path: Path) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise DesignGuideError(f"디자인 가이드에 「{key}」 항목이 없습니다: {path}")
    return value


def _pick(section: dict[str, Any], key: str, path: Path) -> Any:  # noqa: ANN401
    """항목 하나. **없으면 멈춘다** — 그럴듯한 기본값으로 넘어가지 않는다."""
    if key not in section:
        raise DesignGuideError(f"디자인 가이드 항목이 빠졌습니다 — {key}: {path}")
    return section[key]


@lru_cache(maxsize=1)
def load_design_guide(path: str | None = None) -> DesignGuide:
    """가이드를 읽는다. **코드에 값을 두지 않는다.**

    Raises:
        DesignGuideError: 파일이 없거나 항목이 빠졌을 때. 그럴듯한 기본값으로
            조용히 넘어가면 가이드를 고쳐도 반영되지 않는 사고가 난다.
    """
    target = Path(path) if path is not None else design_path()
    if not target.is_file():
        raise DesignGuideError(
            f"PPT 디자인 가이드가 없습니다: {target}. 저장소에서 복원하십시오. "
            "**코드에 색·크기 기본값을 두지 않으므로 이 파일 없이는 만들 수 없습니다.**"
        )
    payload = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise DesignGuideError(f"디자인 가이드가 객체가 아닙니다: {target}")

    slide = _section(payload, "slide", target)
    font = _section(payload, "font", target)
    scale = _section(payload, "type_scale", target)
    colors = _section(payload, "colors", target)
    chart = _section(payload, "chart", target)

    candidates = tuple(str(name) for name in _pick(font, "candidates", target))
    band = {str(key): str(value) for key, value in _pick(chart, "band", target).items()}
    return DesignGuide(
        slide=SlideGeometry(
            width_in=float(_pick(slide, "width_in", target)),
            height_in=float(_pick(slide, "height_in", target)),
            margin_in=float(_pick(slide, "margin_in", target)),
            block_gap_in=float(_pick(slide, "block_gap_in", target)),
            title_gap_in=float(_pick(slide, "title_gap_in", target)),
            text_slack=float(_pick(slide, "text_slack", target)),
            rule_pt=float(_pick(slide, "rule_pt", target)),
        ),
        typography=Typography(
            candidates=candidates,
            fallback=str(_pick(font, "fallback", target)),
        ),
        type_scale=TypeScale(
            cover=float(_pick(scale, "cover", target)),
            section=float(_pick(scale, "section", target)),
            slide_title=float(_pick(scale, "slide_title", target)),
            card_title=float(_pick(scale, "card_title", target)),
            body=float(_pick(scale, "body", target)),
            caption=float(_pick(scale, "caption", target)),
        ),
        colors=Palette(
            cover_background=str(_pick(colors, "cover_background", target)),
            closing_background=str(_pick(colors, "closing_background", target)),
            deep_green=str(_pick(colors, "deep_green", target)),
            dark_primary=str(_pick(colors, "dark_primary", target)),
            white=str(_pick(colors, "white", target)),
            coral=str(_pick(colors, "coral", target)),
            blue=str(_pick(colors, "blue", target)),
            ink=str(_pick(colors, "ink", target)),
            muted=str(_pick(colors, "muted", target)),
            rule=str(_pick(colors, "rule", target)),
            on_dark=str(_pick(colors, "on_dark", target)),
            on_dark_muted=str(_pick(colors, "on_dark_muted", target)),
            on_dark_rule=str(_pick(colors, "on_dark_rule", target)),
        ),
        chart=ChartPalette(
            canvas=str(_pick(chart, "canvas", target)),
            text=str(_pick(chart, "text", target)),
            grid=str(_pick(chart, "grid", target)),
            series=tuple(str(item) for item in _pick(chart, "series", target)),
            day_series=tuple(str(item) for item in _pick(chart, "day_series", target)),
            band=band,
            base_fee=str(_pick(chart, "base_fee", target)),
            saving=str(_pick(chart, "saving", target)),
            increase=str(_pick(chart, "increase", target)),
            neutral=str(_pick(chart, "neutral", target)),
            highlight=str(_pick(chart, "highlight", target)),
            fill=str(_pick(chart, "fill", target)),
            fill_alpha=float(_pick(chart, "fill_alpha", target)),
        ),
        updated=str(payload.get("updated", "")),
    )
