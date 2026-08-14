"""보고서 차트 (요구사항서 10.5).

Word 에 넣을 png 를 만든다. 표는 여기서 그리지 않는다 — **표는 Word 표 객체**여야
자사 제안서로 복사해 쓸 수 있다 (10.5). 그림은 차트만이다.

**한글 폰트를 명시한다.** matplotlib 기본 폰트에는 한글 글리프가 없어 축 이름과
범례가 통째로 두부(□)가 된다. 화면에서는 안 쓰이는 경로라 조용히 깨진 채
산출물로 나간다.

파일로 쓰지 않고 **바이트로 돌려준다.** 보고서 한 벌에 그림이 넷인데 임시 파일을
만들면 지우는 책임이 생긴다 (8세션 결정과 같은 규약).
"""

from __future__ import annotations

import io
import logging
from functools import lru_cache
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # GUI 없는 환경에서 돈다. import 순서를 지킨다.

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib import font_manager
from matplotlib.figure import Figure

from kwise.compare import ComparisonResult
from kwise.diagnose import PeakProfile
from kwise.diagnose.dr import DrProfile
from kwise.io import UsageData
from kwise.measures import DispatchResult, PowerFactorResult, TariffSwitchResult
from kwise.report.days import RepresentativeDay
from kwise.report.frames import (
    DAY_TYPE_LABELS,
    PEAK_ZOOM_HOURS,
    combination_frame,
    dr_daily_frame,
    ess_day_frame,
    hourly_profile_frame,
    monthly_peak_frame,
    peak_window,
    power_triangle_frame,
    solar_annual_frame,
    solar_day_frame,
    surplus_daily_frame,
    tariff_delta_frame,
    tariff_option_frame,
    top_hour_frame,
)

__all__ = [
    "FALLBACK_FONT",
    "FIGURE_DPI",
    "KOREAN_FONT_CANDIDATES",
    "add_legend",
    "apply_style",
    "combination_png",
    "dr_daily_png",
    "ess_day_png",
    "hourly_profile_png",
    "korean_font",
    "monthly_peak_png",
    "power_triangle_png",
    "render_png",
    "solar_annual_png",
    "solar_day_png",
    "surplus_daily_png",
    "tariff_option_png",
    "top_hour_png",
]

# ===================================================================== 24세션 · 한글 폰트
#
# **png 는 서버에서 굽는다.** 서버에 그 폰트가 없으면 한글이 네모(두부)로 나온다 —
# 윈도우 개발 PC 에서는 맑은 고딕이 있어 드러나지 않다가, 리눅스 배포지에서만
# 깨진다. 한 이름을 박아 두는 대신 **설치된 것 중에서 고른다.**
#
#     윈도우   맑은 고딕
#     macOS    애플 SD 산돌고딕 / 애플고딕
#     리눅스   나눔고딕 · 본고딕(Noto)  ← ``packages.txt`` 의 ``fonts-nanum``
#
# Word 문서(:mod:`kwise.report.document`)는 사정이 다르다 — 글꼴 **이름만** 적고
# 그리는 것은 읽는 사람의 Word 라, 없으면 그쪽이 알아서 대체한다.

_log = logging.getLogger(__name__)

#: 찾을 순서. **앞에 있는 것이 이긴다.**
KOREAN_FONT_CANDIDATES: tuple[str, ...] = (
    "Malgun Gothic",  # 윈도우 내장
    "Apple SD Gothic Neo",  # macOS
    "AppleGothic",
    "NanumGothic",  # 리눅스 — fonts-nanum
    "NanumBarunGothic",
    "Noto Sans CJK KR",  # 리눅스 — fonts-noto-cjk
    "Noto Sans KR",
    "Source Han Sans KR",
    "Gulim",
    "Batang",
)

#: 하나도 없을 때 쓸 이름. 한글은 깨지지만 **그림은 나온다** — 멈추지 않는다.
FALLBACK_FONT = "sans-serif"
FIGURE_DPI = 150
_SIZE = (9.0, 3.6)
_COLORS = ("#08519c", "#9ecae1", "#f16913", "#6baed6")
_DAY_COLORS = ("#08519c", "#6baed6", "#fd8d3c", "#d94801")


#: 범례 규약 — **바깥 오른쪽·배경 없음** (23세션 1절).
#:
#: 화면(altair)과 **같은 규약을 쓴다.** 한쪽만 고치면 화면과 보고서가 어긋나
#: 나란히 놓고서야 드러난다 (13세션에 겪었다). altair 쪽은
#: :data:`kwise.ui.charts.LEGEND` 다.
LEGEND_STYLE: dict[str, object] = {
    "loc": "upper left",
    "bbox_to_anchor": (1.01, 1.0),
    "frameon": False,
    "fontsize": 8,
    "borderaxespad": 0.0,
}


def add_legend(axes: object, **overrides: object) -> None:
    """범례를 **그림 바깥 오른쪽**에 단다. 배경 상자를 두지 않는다."""
    axes.legend(**{**LEGEND_STYLE, **overrides})  # type: ignore[attr-defined]


#: 폰트 **파일 이름**에서 한글 글꼴을 알아보는 실마리. 캐시가 낡았을 때만 쓴다.
_FONT_FILE_HINTS = ("nanum", "noto", "malgun", "gothic", "gulim", "batang")


def _register_system_fonts() -> None:
    """설치된 한글 폰트 **파일**을 matplotlib 에 등록한다.

    ``findSystemFonts`` · ``addfont`` 는 공개 API 다 — 폰트 목록을 통째로 다시
    만드는 비공개 함수에 기대지 않는다.
    """
    known = {item.fname for item in font_manager.fontManager.ttflist}
    for path in font_manager.findSystemFonts():
        if path in known:
            continue
        if any(hint in Path(path).name.lower() for hint in _FONT_FILE_HINTS):
            try:
                font_manager.fontManager.addfont(path)
            except (OSError, RuntimeError):  # pragma: no cover - 깨진 폰트 파일
                continue


@lru_cache(maxsize=1)
def korean_font() -> str:
    """**설치된** 한글 폰트 하나. 없으면 :data:`FALLBACK_FONT`.

    matplotlib 은 import 할 때 폰트 목록을 **캐시에서** 읽는다. 배포지에서 방금
    설치한 폰트(``packages.txt`` 의 ``fonts-nanum``)가 그 캐시에 없을 수 있어,
    **한 번 못 찾으면 파일을 직접 찾아 등록하고** 그래도 없을 때만 물러선다.
    """
    for rescan in (False, True):
        if rescan:
            _register_system_fonts()
        installed = {item.name for item in font_manager.fontManager.ttflist}
        for name in KOREAN_FONT_CANDIDATES:
            if name in installed:
                return name
    _log.warning(
        "한글 폰트를 찾지 못해 그림의 한글이 깨집니다. 후보: %s",
        ", ".join(KOREAN_FONT_CANDIDATES),
    )
    return FALLBACK_FONT


def apply_style() -> None:
    """한글 폰트와 눈금 스타일. **그릴 때마다 건다** (rcParams 는 전역이다)."""
    plt.rcParams["font.family"] = korean_font()
    # 한글 폰트에는 유니코드 마이너스가 없다. 음수 축이 깨진다.
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["axes.grid"] = True
    plt.rcParams["grid.alpha"] = 0.3
    plt.rcParams["axes.spines.top"] = False
    plt.rcParams["axes.spines.right"] = False


def render_png(figure: Figure) -> bytes:
    """그림을 png 바이트로 굽고 닫는다. **닫지 않으면 메모리에 쌓인다.**"""
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", dpi=FIGURE_DPI, bbox_inches="tight")
    plt.close(figure)
    return buffer.getvalue()


def hourly_profile_png(peak: PeakProfile) -> bytes:
    """시간대별 평균 부하 프로파일 (2장 · 부하 패턴)."""
    apply_style()
    frame = hourly_profile_frame(peak)
    figure, axes = plt.subplots(figsize=_SIZE)
    axes.plot(frame["시각"], frame["평균 부하(kW)"], marker="o", color=_COLORS[0], linewidth=1.8)
    axes.set_ylabel("평균 부하 (kW)")
    axes.set_xlabel("시각 (검침 라벨)")
    axes.tick_params(axis="x", rotation=90, labelsize=8)
    return render_png(figure)


def monthly_peak_png(peak: PeakProfile) -> bytes:
    """월별 최대수요와 요금적용전력 (2장 · 피크 특성).

    **관측 최대와 요금적용 대상 최대를 나란히 둔다.** 둘이 벌어지는 건물은
    "밤 피크는 요금적용전력이 아니다" 가 이 그림 하나로 보인다 (5.2 ①).
    """
    apply_style()
    frame = monthly_peak_frame(peak)
    positions = range(len(frame))
    width = 0.4
    figure, axes = plt.subplots(figsize=_SIZE)
    axes.bar(
        [pos - width / 2 for pos in positions],
        frame["관측 최대(kW)"],
        width=width,
        label="관측 최대",
        color=_COLORS[0],
    )
    axes.bar(
        [pos + width / 2 for pos in positions],
        frame["요금적용 대상 최대(kW)"],
        width=width,
        label="요금적용 대상 최대",
        color=_COLORS[1],
    )
    axes.axhline(
        peak.billing_demand_kw,
        color="crimson",
        linestyle="--",
        linewidth=1.2,
        label=f"요금적용전력 {peak.billing_demand_kw:,.0f} kW",
    )
    axes.set_xticks(list(positions))
    axes.set_xticklabels(frame["월"], rotation=45, ha="right", fontsize=8)
    axes.set_ylabel("최대수요 (kW)")
    add_legend(axes)
    return render_png(figure)


def top_hour_png(peak: PeakProfile) -> bytes:
    """상위 구간 시각 분포 (2장 · 피크 특성).

    **태양광 기여 가능성을 즉시 보여 주는 지표다** (6.2). 두 벌을 함께 그린다 —
    전 슬롯 기준(청구서 관행)과 요금적용전력 대상 기준(판정용)이다.
    """
    apply_style()
    frame = top_hour_frame(peak)
    positions = range(len(frame))
    width = 0.4
    figure, axes = plt.subplots(figsize=_SIZE)
    axes.bar(
        [pos - width / 2 for pos in positions],
        frame["전 슬롯"],
        width=width,
        label="전 슬롯",
        color=_COLORS[0],
    )
    axes.bar(
        [pos + width / 2 for pos in positions],
        frame["요금적용전력 대상"],
        width=width,
        label="요금적용전력 대상",
        color=_COLORS[2],
    )
    axes.set_xticks(list(positions))
    axes.set_xticklabels(frame["시각"], rotation=90, fontsize=8)
    axes.set_ylabel("구간 수")
    axes.set_xlabel(f"상위 {peak.top_n}구간 발생 시각 (검침 라벨)")
    add_legend(axes)
    return render_png(figure)


def combination_png(comparison: ComparisonResult) -> bytes:
    """조합별 절감액과 투자비 (4장).

    **투자비를 모르는 조합은 막대를 그리지 않고 그 사실을 적는다.** 0 으로
    그리면 "공짜" 로 읽힌다 (7.5).
    """
    apply_style()
    frame = combination_frame(comparison)
    positions = range(len(frame))
    height = 0.38
    figure, axes = plt.subplots(figsize=(9.0, max(2.4, 0.9 * len(frame))))
    axes.barh(
        [pos + height / 2 for pos in positions],
        frame["절감액(원)"] / 1e8,
        height=height,
        label="절감액",
        color=_COLORS[0],
    )
    investment = frame["투자비(원)"].fillna(0.0) / 1e8
    axes.barh(
        [pos - height / 2 for pos in positions],
        investment,
        height=height,
        label="투자비",
        color=_COLORS[3],
    )
    for index, value in enumerate(frame["투자비(원)"]):
        if value is None or value != value:  # NaN
            axes.text(0.0, index - height / 2, " 투자비 미산출", va="center", fontsize=8)
    axes.set_yticks(list(positions))
    axes.set_yticklabels(frame["조합"], fontsize=9)
    axes.invert_yaxis()
    axes.set_xlabel("억원")
    add_legend(axes)
    return render_png(figure)


# ===================================================================== 15세션 · 수단별 차트
#
# 화면(altair)과 **같은 프레임**을 본다 (:mod:`kwise.report.frames`). 각자 만들면
# 같은 이름의 차트가 서로 다른 수를 그리게 되고, 그 어긋남은 눈으로 잡히지 않는다.


def tariff_option_png(switch: TariffSwitchResult) -> bytes:
    """요금제별 기본·전력량·합계 **그룹 막대**와 현행 대비 차액 (3장 · 7.1).

    **쌓지 않고 나란히 세운다** (17세션 1-2). 누적은 합계만 보이고 기본요금끼리·
    전력량요금끼리 견줄 수가 없는데, 선택요금은 바로 그 둘을 맞바꾸는 제도다.

    아래 칸에 **현행 대비 차액**을 따로 낸다 (1-3). 35억 위의 5천만원은 같은
    축에서 보이지 않는다.
    """
    apply_style()
    frame = tariff_option_frame(switch)
    delta = tariff_delta_frame(switch)
    positions = np.arange(len(frame), dtype=float)
    figure, (upper, lower) = plt.subplots(
        2, 1, figsize=(_SIZE[0], _SIZE[1] * 1.45), height_ratios=(3, 1), sharex=True
    )

    width = 0.26
    series = (
        ("기본요금", frame["기본요금(원)"], _COLORS[0]),
        ("전력량요금", frame["전력량요금(원)"], _COLORS[1]),
        ("합계", frame["합계(원)"], _COLORS[2]),
    )
    for index, (label, values, color) in enumerate(series):
        upper.bar(
            positions + (index - 1) * width,
            values.fillna(0.0) / 1e8,
            width=width,
            label=label,
            color=color,
        )
    # **축을 0 부터 시작하지 않는다** (17세션 0절).
    finite = [value / 1e8 for value in frame["합계(원)"] if pd.notna(value)]
    finite += [value / 1e8 for value in frame["기본요금(원)"] if pd.notna(value)]
    if finite:
        span = max(finite) - min(finite)
        upper.set_ylim(max(0.0, min(finite) - span * 0.15), max(finite) + span * 0.2)
    upper.set_ylabel("억원")
    add_legend(upper)

    colors = [
        "#31a354" if value < 0 else ("#bdbdbd" if value == 0 else "#de2d26")
        for value in delta["현행 대비(원)"]
    ]
    lower.bar(positions, delta["현행 대비(원)"] / 1e8, width=0.5, color=colors)
    lower.axhline(0.0, color="#525252", linewidth=1.0)
    for index, value in enumerate(delta["현행 대비(원)"]):
        lower.text(
            index,
            value / 1e8,
            "현행" if abs(value) < 1 else f"{value / 1e8:,.2f}억",
            ha="center",
            va="top" if value < 0 else "bottom",
            fontsize=8,
        )
    lower.set_ylabel("현행 대비 (억원)")
    lower.set_xticks(list(positions))
    lower.set_xticklabels(
        [
            f"{name}\n{mark}" if mark else name
            for name, mark in zip(frame["요금제"], frame["표식"], strict=True)
        ],
        fontsize=9,
    )
    return render_png(figure)


def dr_daily_png(profile: DrProfile) -> bytes:
    """연간 일별 운영시간대 평균 부하 (3장 · 7.3).

    **기준선 근처로 내려온 평일이 감축 가능일이다.**
    """
    apply_style()
    frame = dr_daily_frame(profile)
    figure, axes = plt.subplots(figsize=_SIZE)
    palette = dict(zip(DAY_TYPE_LABELS.values(), _DAY_COLORS, strict=True))
    for kind, group in frame.groupby("구분", sort=False):
        axes.scatter(
            group["날짜"],
            group["운영시간대 평균(kW)"],
            s=8,
            label=str(kind),
            color=palette.get(str(kind), _COLORS[0]),
        )
    for value, name in (
        (profile.weekend_baseline_kw, "주말·공휴일 평균"),
        (profile.low_load_threshold_kw, "저부하 문턱"),
    ):
        if value is not None:
            axes.axhline(value, color="crimson", linestyle="--", linewidth=1.0, label=name)
    low = frame[frame["저부하 평일"]]
    if len(low):
        axes.scatter(
            low["날짜"],
            low["운영시간대 평균(kW)"],
            s=70,
            marker="v",
            color="crimson",
            label="저부하 평일",
        )
    axes.set_ylabel("운영 시간대 평균 부하 (kW)")
    axes.tick_params(axis="x", rotation=45, labelsize=8)
    add_legend(axes, ncol=3)
    return render_png(figure)


def power_triangle_png(result: PowerFactorResult) -> bytes:
    """전력삼각형 — 개선 전후 (3장 · 7.4). **각이 좁아지는 모습**이 전부다."""
    apply_style()
    frame = power_triangle_frame(result)
    figure, axes = plt.subplots(figsize=(4.4, 3.4))
    for color, (_, row) in zip(_COLORS, frame.iterrows(), strict=False):
        axes.plot(
            [0.0, row["유효전력"], row["유효전력"], 0.0],
            [0.0, 0.0, row["무효전력"], 0.0],
            color=color,
            linewidth=1.8,
            label=f"{row['구분']} — 역률 {row['역률(%)']:.0f}% · {row['각도(도)']:.0f}°",
        )
    axes.set_xlabel("유효전력 (기준 1)")
    axes.set_ylabel("무효전력")
    add_legend(axes)
    return render_png(figure)


def solar_day_png(usage: UsageData, generation_kw: pd.Series, day: RepresentativeDay) -> bytes:
    """대표일의 원부하·순부하와 **그 사이 저감분** (3장 · 7.5 · 17세션 3-5).

    **축을 0 부터 시작하지 않고 두 선 사이를 채운다.** 5,000 kW 대 부하에 수백
    kW 를 얹으면 0 부터 그린 축에서 두 선이 붙어 보인다.
    """
    apply_style()
    frame = solar_day_frame(usage, generation_kw, day.date)
    figure, axes = plt.subplots(figsize=_SIZE)
    if len(frame):
        axes.fill_between(
            frame["시각"],
            frame["순부하(kW)"],
            frame["원부하(kW)"],
            color="#31a354",
            alpha=0.6,
            label="저감분",
        )
    for name, color in (("원부하(kW)", _COLORS[1]), ("순부하(kW)", _COLORS[0])):
        axes.plot(frame["시각"], frame[name], label=name, color=color, linewidth=1.6)
    if len(frame):
        low = float(frame["순부하(kW)"].min())
        high = float(frame["원부하(kW)"].max())
        margin = max((high - low) * 0.15, 1.0)
        axes.set_ylim(low - margin, high + margin)
        peak = frame.loc[frame["원부하(kW)"].idxmax()]
        cut = float(peak["원부하(kW)"]) - float(peak["순부하(kW)"])
        # **빼기표(U+2212)를 쓰지 않는다.** Malgun Gothic 에 글리프가 없어 png 에서
        # 두부(□)가 된다 — `apply_style` 의 ``axes.unicode_minus = False`` 와 같은 이유다.
        axes.annotate(
            f"피크 -{cut:,.0f} kW",
            xy=(peak["시각"], float(peak["원부하(kW)"])),
            xytext=(0, 14),
            textcoords="offset points",
            ha="center",
            fontsize=8,
            color="crimson",
            arrowprops={"arrowstyle": "->", "color": "crimson"},
        )
    axes.set_ylabel("출력 (kW)")
    axes.set_xlabel(f"{day.title} · 15분")
    axes.tick_params(axis="x", rotation=45, labelsize=8)
    add_legend(axes)
    return render_png(figure)


def solar_annual_png(usage: UsageData, generation_kw: pd.Series) -> bytes:
    """연간 **일별 발전량** (3장 · 7.5 · 23세션 5절).

    **화면과 같은 그림이다.** 넷을 한 축에 얹었더니 사용량(일 60 MWh 대)에
    눌려 발전량(3 MWh 대)이 보이지 않았다 — 한 그림은 한 가지만 말한다.
    """
    apply_style()
    frame = solar_annual_frame(usage, generation_kw)
    figure, axes = plt.subplots(figsize=_SIZE)
    axes.fill_between(
        frame["날짜"], 0.0, frame["발전량(kWh)"], color="#31a354", alpha=0.85, label="발전량"
    )
    axes.set_ylabel("일별 발전량 (kWh)")
    axes.tick_params(axis="x", rotation=45, labelsize=8)
    add_legend(axes)
    return render_png(figure)


def ess_day_png(
    usage: UsageData,
    dispatch: DispatchResult,
    day: RepresentativeDay,
    *,
    bands: pd.Series | None = None,
    zoom: bool = True,
) -> bytes:
    """대표일의 ESS — **피크 앞뒤만 확대한 한 칸** (3장 · 7.6 · 23세션 6절).

    **화면과 같은 그림이다.** 17세션의 아래 칸(충·방전 막대)은 문구로 내렸다 —
    위 칸과 종속이라 그림이 둘일 필요가 없다.
    """
    apply_style()
    frame = ess_day_frame(usage, dispatch, day.date, bands=bands)
    title = f"{day.title} · 15분"
    if len(frame) and zoom:
        frame = peak_window(frame)
        title = f"{day.title} · 피크 앞뒤 {PEAK_ZOOM_HOURS}시간"

    figure, axes = plt.subplots(figsize=_SIZE)
    if len(frame):
        axes.fill_between(
            frame["시각"],
            frame["순부하(kW)"],
            frame["원부하(kW)"],
            color="#31a354",
            alpha=0.6,
            label="저감분",
        )
    axes.plot(frame["시각"], frame["원부하(kW)"], label="원부하", color=_COLORS[1], linewidth=1.4)
    axes.plot(frame["시각"], frame["순부하(kW)"], label="순부하", color=_COLORS[0], linewidth=1.6)
    axes.axhline(
        dispatch.target_kw,
        color="crimson",
        linestyle="--",
        linewidth=1.2,
        label=f"목표 {dispatch.target_kw:,.0f} kW",
    )
    if len(frame):
        low = min(float(frame["순부하(kW)"].min()), dispatch.target_kw)
        high = float(frame["원부하(kW)"].max())
        margin = max((high - low) * 0.15, 1.0)
        axes.set_ylim(low - margin, high + margin)
    axes.set_ylabel("부하 (kW)")
    axes.set_xlabel(title)
    axes.tick_params(axis="x", rotation=45, labelsize=8)
    add_legend(axes)
    return render_png(figure)


def surplus_daily_png(usage: UsageData, surplus_kw: pd.Series) -> bytes:
    """연간 일별 잉여량 (3장 · 7.7). **주말에 몰리는지**가 보여야 한다."""
    apply_style()
    frame = surplus_daily_frame(usage, surplus_kw)
    figure, axes = plt.subplots(figsize=_SIZE)
    palette = {"평일": _COLORS[0], "토요일": _COLORS[3], "일요일": _COLORS[2]}
    for kind, group in frame.groupby("구분", sort=False):
        axes.bar(
            group["날짜"],
            group["잉여(kWh)"],
            label=str(kind),
            color=palette.get(str(kind), _COLORS[0]),
            width=1.0,
        )
    axes.set_ylabel("일별 잉여 (kWh)")
    axes.tick_params(axis="x", rotation=45, labelsize=8)
    add_legend(axes)
    return render_png(figure)
