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

import datetime as dt
import io
import logging
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # GUI 없는 환경에서 돈다. import 순서를 지킨다.

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib import font_manager
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter

from kwise.compare import ComparisonResult
from kwise.diagnose import ChargeStructure, PeakProfile
from kwise.diagnose.dr import DrProfile
from kwise.io import UsageData
from kwise.measures import (
    DispatchResult,
    PowerFactorResult,
    TariffSwitchResult,
)
from kwise.report.days import RepresentativeDay
from kwise.report.design import ChartPalette, load_design_guide
from kwise.report.frames import (
    BAND_LABELS,
    DAY_TYPE_LABELS,
    PEAK_ZOOM_HOURS,
    band_frame,
    combination_frame,
    daily_temperature_frame,
    daily_usage_frame,
    dr_daily_frame,
    ess_day_frame,
    hourly_profile_frame,
    month_labels,
    monthly_charge_frame,
    monthly_peak_frame,
    peak_window,
    power_factor_day_frame,
    power_triangle_frame,
    solar_annual_frame,
    solar_day_frame,
    surplus_daily_frame,
    tariff_delta_frame,
    tariff_option_frame,
    temperature_mean_frame,
    top_hour_frame,
)

__all__ = [
    "DONUT_GRID",
    "FALLBACK_FONT",
    "FIGURE_DPI",
    "KOREAN_FONT_CANDIDATES",
    "add_legend",
    "apply_style",
    "band_donut_grid_png",
    "chart_palette",
    "combination_png",
    "daily_temperature_png",
    "daily_usage_png",
    "date_axis",
    "delta_label_place",
    "dr_daily_png",
    "ess_day_png",
    "hourly_profile_png",
    "korean_date_label",
    "korean_font",
    "monthly_charge_png",
    "monthly_peak_png",
    "power_factor_day_png",
    "power_triangle_png",
    "render_png",
    "solar_annual_png",
    "solar_day_png",
    "surplus_daily_png",
    "tariff_option_png",
    "time_axis",
    "top_hour_png",
]

# ===================================================================== 25세션 · 한글 폰트
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


# ===================================================================== 36세션 4절 · png 팔레트
#
# **색을 여기에 박지 않는다.** 값은 ``data\\ppt_design.json`` 한 곳에 있고
# :mod:`kwise.report.design` 이 읽는다 (36세션 3절). 가이드가 바뀌면 고칠 자리가
# 하나여야 한다 — png 와 슬라이드가 따로 색을 정하면 한 장에 나란히 놓였을 때
# 어긋나고, 그 어긋남은 파일을 열어 보고서야 드러난다.
#
# **화면(altair)은 손대지 않는다.** 조건이 다르다 —
#
#     화면   다크 모드를 탄다. 그래서 **글자에 중립색(흰·회·검)을 박지 않는다**
#            (35세션 1-2). 한쪽 모드가 반드시 깨지기 때문이다
#     png    **흰 캔버스가 확정이다.** 배경이 하나뿐이라 그 규약이 설 자리가 없고,
#            오히려 어두운 중립색이라야 읽힌다
#
# 대신 png 에는 **배경을 확정하는 규약**이 따로 선다 — :func:`render_png` 가
# 캔버스를 투명으로 굽지 않는다. 투명하게 두면 넣는 쪽 배경이 무엇이냐에 따라
# 글자가 사라져, 화면이 겪던 병을 png 가 대신 앓는다.


def chart_palette() -> ChartPalette:
    """png 팔레트 (36세션 4절). 가이드 파일에서 온다."""
    return load_design_guide().chart


def _series() -> tuple[str, ...]:
    return chart_palette().series


def _day_series() -> tuple[str, ...]:
    return chart_palette().day_series


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
    """한글 폰트와 눈금 스타일. **그릴 때마다 건다** (rcParams 는 전역이다).

    글자·눈금·격자 색도 **가이드에서 온다** (36세션 4절). 흰 캔버스가 확정이라
    어두운 중립색을 쓴다 — 화면의 다크 모드 규약은 여기에 적용되지 않는다.
    """
    marks = chart_palette()
    plt.rcParams["font.family"] = korean_font()
    # 한글 폰트에는 유니코드 마이너스가 없다. 음수 축이 깨진다.
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["axes.grid"] = True
    plt.rcParams["grid.alpha"] = 0.3
    plt.rcParams["grid.color"] = marks.grid
    plt.rcParams["axes.spines.top"] = False
    plt.rcParams["axes.spines.right"] = False
    plt.rcParams["axes.edgecolor"] = marks.grid
    for key in ("text.color", "axes.labelcolor", "xtick.color", "ytick.color"):
        plt.rcParams[key] = marks.text


# ===================================================================== 33세션 1절 · 날짜 표기
#
# **matplotlib 도 기본 로케일이 영어다.** 화면(vega)만 고치면 보고서 png 에는
# ``May`` 가 남아 두 그림이 어긋난다 (13세션에 겪은 그 병이다). 규칙은 화면과
# 같다 — 1월 1일은 「2024년」, 달의 첫날은 「5월」, 나머지는 「4월 30일」.
#
# ``%-m`` 은 Windows 의 strftime 이 모르므로 **직접 만든다.**


def korean_date_label(stamp: dt.datetime) -> str:
    """날짜 눈금 하나 (33세션 1절). 화면의 ``DATE_LABEL_EXPR`` 와 같은 규칙이다."""
    if stamp.month == 1 and stamp.day == 1:
        return f"{stamp.year}년"
    if stamp.day == 1:
        return f"{stamp.month}월"
    return f"{stamp.month}월 {stamp.day}일"


def date_axis(axes: Axes) -> None:
    """날짜 축을 한국식 눈금으로 (33세션 1절)."""
    axes.xaxis.set_major_formatter(
        FuncFormatter(lambda value, _pos: korean_date_label(mdates.num2date(value)))
    )


def time_axis(axes: Axes) -> None:
    """하루 안 축을 ``시:분`` 으로 (33세션 1절). 날짜는 그림 제목이 적는다."""
    axes.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))


def render_png(figure: Figure) -> bytes:
    """그림을 png 바이트로 굽고 닫는다. **닫지 않으면 메모리에 쌓인다.**

    **캔버스를 투명으로 굽지 않는다** (36세션 4절). 배경을 비워 두면 넣는 쪽
    바탕이 무엇이냐에 따라 어두운 글자가 사라진다 — png 의 글자색 규약은
    「흰 바탕이 확정」 위에 서 있으므로, 그 확정을 여기서 지킨다.
    """
    canvas = chart_palette().canvas
    buffer = io.BytesIO()
    figure.savefig(
        buffer,
        format="png",
        dpi=FIGURE_DPI,
        bbox_inches="tight",
        facecolor=canvas,
        edgecolor="none",
    )
    plt.close(figure)
    return buffer.getvalue()


def hourly_profile_png(peak: PeakProfile, *, size: tuple[float, float] | None = None) -> bytes:
    """시간대별 평균 부하 프로파일 (2장 · 부하 패턴).

    ``size`` 는 **슬라이드가 쓴다** (36세션). Word 의 가로 긴 비율(9:3.6)은 문서
    한 단에 맞춘 것이라 16:9 칸에 넣으면 위아래가 남는다.
    """
    apply_style()
    frame = hourly_profile_frame(peak)
    figure, axes = plt.subplots(figsize=size or _SIZE)
    axes.plot(frame["시각"], frame["평균 부하(kW)"], marker="o", color=_series()[0], linewidth=1.8)
    axes.set_ylabel("평균 부하 (kW)")
    axes.set_xlabel("시각 (검침 라벨)")
    axes.tick_params(axis="x", rotation=90, labelsize=8)
    return render_png(figure)


def monthly_peak_png(peak: PeakProfile, *, size: tuple[float, float] | None = None) -> bytes:
    """월별 최대수요와 요금적용전력 (2장 · 피크 특성).

    **관측 최대와 요금적용 대상 최대를 나란히 둔다.** 둘이 벌어지는 건물은
    "밤 피크는 요금적용전력이 아니다" 가 이 그림 하나로 보인다 (5.2 ①).
    """
    apply_style()
    frame = monthly_peak_frame(peak)
    positions = range(len(frame))
    width = 0.4
    figure, axes = plt.subplots(figsize=size or _SIZE)
    axes.bar(
        [pos - width / 2 for pos in positions],
        frame["관측 최대(kW)"],
        width=width,
        label="관측 최대",
        color=_series()[0],
    )
    axes.bar(
        [pos + width / 2 for pos in positions],
        frame["요금적용 대상 최대(kW)"],
        width=width,
        label="요금적용 대상 최대",
        color=_series()[1],
    )
    axes.axhline(
        peak.billing_demand_kw,
        color=chart_palette().highlight,
        linestyle="--",
        linewidth=1.2,
        label=f"요금적용전력 {peak.billing_demand_kw:,.0f} kW",
    )
    axes.set_xticks(list(positions))
    axes.set_xticklabels(frame["월"], rotation=45, ha="right", fontsize=8)
    axes.set_ylabel("최대수요 (kW)")
    add_legend(axes)
    return render_png(figure)


def top_hour_png(peak: PeakProfile, *, size: tuple[float, float] | None = None) -> bytes:
    """상위 구간 시각 분포 (2장 · 피크 특성).

    **태양광 기여 가능성을 즉시 보여 주는 지표다** (6.2). 두 벌을 함께 그린다 —
    전 슬롯 기준(청구서 관행)과 요금적용전력 대상 기준(판정용)이다.
    """
    apply_style()
    frame = top_hour_frame(peak)
    positions = range(len(frame))
    width = 0.4
    figure, axes = plt.subplots(figsize=size or _SIZE)
    axes.bar(
        [pos - width / 2 for pos in positions],
        frame["전 슬롯"],
        width=width,
        label="전 슬롯",
        color=_series()[0],
    )
    axes.bar(
        [pos + width / 2 for pos in positions],
        frame["요금적용전력 대상"],
        width=width,
        label="요금적용전력 대상",
        color=_series()[2],
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
        color=_series()[0],
    )
    investment = frame["투자비(원)"].fillna(0.0) / 1e8
    axes.barh(
        [pos - height / 2 for pos in positions],
        investment,
        height=height,
        label="투자비",
        color=_series()[3],
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


def delta_label_place(value: float, pad: float) -> tuple[float, str]:
    """차액 라벨의 자리 — **0 선 반대쪽** (53세션 5절).

    막대 끝에 붙여 두면 파란 막대 위에 검은 글씨가 얹혀 읽히지 않는다.
    0 선 건너편은 어느 자료에서도 비어 있으므로 겹칠 일이 없다.

    Returns:
        (y 좌표, ``va``). 줄어드는 쪽(음수)이면 선 위, 늘어나는 쪽이면 선 아래다.
    """
    if value <= 0:
        return pad * 0.35, "bottom"
    return -pad * 0.35, "top"


def tariff_option_png(
    switch: TariffSwitchResult, *, size: tuple[float, float] | None = None
) -> bytes:
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
    shape = size or (_SIZE[0], _SIZE[1] * 1.45)
    figure, (upper, lower) = plt.subplots(2, 1, figsize=shape, height_ratios=(3, 1), sharex=True)

    width = 0.26
    series = (
        ("기본요금", frame["기본요금(원)"], _series()[0]),
        ("전력량요금", frame["전력량요금(원)"], _series()[1]),
        ("합계", frame["합계(원)"], _series()[2]),
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

    # **줄어드는 쪽과 늘어나는 쪽을 색으로 가른다.** 가이드의 두 포인트색이
    # 그대로 이 뜻을 진다 — 블루가 절감, 코랄이 증가다 (36세션 4절).
    marks = chart_palette()
    colors = [
        marks.saving if value < 0 else (marks.neutral if value == 0 else marks.increase)
        for value in delta["현행 대비(원)"]
    ]
    amounts = [float(value) for value in delta["현행 대비(원)"]]
    scaled = [value / 1e8 for value in amounts]
    lower.bar(positions, scaled, width=0.5, color=colors)
    lower.axhline(0.0, color=chart_palette().text, linewidth=1.0)
    # **차액 라벨을 0 선 반대쪽에 둔다** (53세션 5절). 막대 끝에 붙여 두면 파란
    # 막대 위에 검은 글씨가 얹혀 읽히지 않았다 — 「-0.54억」 이 그랬다.
    # 0 선 건너편은 언제나 비어 있으므로 어느 자료에서도 겹치지 않는다.
    reach = max((abs(value) for value in scaled), default=0.0) or 1.0
    pad = reach * 0.10
    lower.set_ylim(
        min(min(scaled, default=0.0), 0.0) - pad,
        max(max(scaled, default=0.0), 0.0) + pad * 2.4,
    )
    for index, (amount, value) in enumerate(zip(amounts, scaled, strict=True)):
        offset, align = delta_label_place(value, pad)
        lower.text(
            index,
            offset,
            "현행" if abs(amount) < 1 else f"{value:,.2f}억",
            ha="center",
            va=align,
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


def dr_daily_png(profile: DrProfile, *, size: tuple[float, float] | None = None) -> bytes:
    """연간 일별 운영시간대 평균 부하 (3장 · 7.3).

    **기준선 근처로 내려온 평일이 감축 가능일이다.**
    """
    apply_style()
    frame = dr_daily_frame(profile)
    figure, axes = plt.subplots(figsize=size or _SIZE)
    palette = dict(zip(DAY_TYPE_LABELS.values(), _day_series(), strict=True))
    for kind, group in frame.groupby("구분", sort=False):
        axes.scatter(
            group["날짜"],
            group["운영시간대 평균(kW)"],
            s=8,
            label=str(kind),
            color=palette.get(str(kind), _series()[0]),
        )
    for value, name in (
        (profile.weekend_baseline_kw, "주말·공휴일 평균"),
        (profile.low_load_threshold_kw, "저부하 문턱"),
    ):
        if value is not None:
            axes.axhline(
                value, color=chart_palette().highlight, linestyle="--", linewidth=1.0, label=name
            )
    low = frame[frame["저부하 평일"]]
    if len(low):
        axes.scatter(
            low["날짜"],
            low["운영시간대 평균(kW)"],
            s=70,
            marker="v",
            color=chart_palette().highlight,
            label="저부하 평일",
        )
    axes.set_ylabel("운영 시간대 평균 부하 (kW)")
    date_axis(axes)
    axes.tick_params(axis="x", rotation=45, labelsize=8)
    add_legend(axes, ncol=3)
    return render_png(figure)


def power_triangle_png(result: PowerFactorResult) -> bytes:
    """전력삼각형 — 개선 전후 (3장 · 7.4). **각이 좁아지는 모습**이 전부다."""
    apply_style()
    frame = power_triangle_frame(result)
    figure, axes = plt.subplots(figsize=(4.4, 3.4))
    for color, (_, row) in zip(_series(), frame.iterrows(), strict=False):
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


def solar_day_png(
    usage: UsageData,
    generation_kw: pd.Series,
    day: RepresentativeDay,
    *,
    size: tuple[float, float] | None = None,
) -> bytes:
    """대표일의 원부하·순부하와 **그 사이 저감분** (3장 · 7.5 · 17세션 3-5).

    **축을 0 부터 시작하지 않고 두 선 사이를 채운다.** 5,000 kW 대 부하에 수백
    kW 를 얹으면 0 부터 그린 축에서 두 선이 붙어 보인다.
    """
    apply_style()
    frame = solar_day_frame(usage, generation_kw, day.date)
    figure, axes = plt.subplots(figsize=size or _SIZE)
    if len(frame):
        axes.fill_between(
            frame["시각"],
            frame["순부하(kW)"],
            frame["원부하(kW)"],
            color=chart_palette().fill,
            # **띠는 선과 같은 색이라 옅어야 한다** (36세션 4절). 진하게 두면
            # 알파가 섞여 원부하 선과 거의 같은 색이 되어 셋이 갈리지 않는다.
            alpha=chart_palette().fill_alpha,
            label="저감분",
        )
    for name, color in (("원부하(kW)", _series()[1]), ("순부하(kW)", _series()[0])):
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
            color=chart_palette().highlight,
            arrowprops={"arrowstyle": "->", "color": chart_palette().highlight},
        )
    axes.set_ylabel("출력 (kW)")
    axes.set_xlabel(f"{day.title} · 15분")
    time_axis(axes)
    axes.tick_params(axis="x", rotation=45, labelsize=8)
    add_legend(axes)
    return render_png(figure)


def solar_annual_png(
    usage: UsageData, generation_kw: pd.Series, *, size: tuple[float, float] | None = None
) -> bytes:
    """연간 **일별 발전량** (3장 · 7.5 · 23세션 5절).

    **화면과 같은 그림이다.** 넷을 한 축에 얹었더니 사용량(일 60 MWh 대)에
    눌려 발전량(3 MWh 대)이 보이지 않았다 — 한 그림은 한 가지만 말한다.
    """
    apply_style()
    frame = solar_annual_frame(usage, generation_kw)
    figure, axes = plt.subplots(figsize=size or _SIZE)
    axes.fill_between(
        frame["날짜"],
        0.0,
        frame["발전량(kWh)"],
        color=chart_palette().fill,
        alpha=0.85,
        label="발전량",
    )
    axes.set_ylabel("일별 발전량 (kWh)")
    date_axis(axes)
    axes.tick_params(axis="x", rotation=45, labelsize=8)
    add_legend(axes)
    return render_png(figure)


def ess_day_png(
    usage: UsageData,
    dispatch: DispatchResult,
    day: RepresentativeDay,
    *,
    zoom: bool = True,
    size: tuple[float, float] | None = None,
) -> bytes:
    """대표일의 ESS — **피크 앞뒤만 확대한 한 칸** (3장 · 7.6 · 23세션 6절).

    **화면과 같은 그림이다.** 17세션의 아래 칸(충·방전 막대)은 문구로 내렸다 —
    위 칸과 종속이라 그림이 둘일 필요가 없다.
    """
    apply_style()
    frame = ess_day_frame(usage, dispatch, day.date)
    title = f"{day.title} · 15분"
    if len(frame) and zoom:
        frame = peak_window(frame)
        title = f"{day.title} · 피크 앞뒤 {PEAK_ZOOM_HOURS}시간"

    figure, axes = plt.subplots(figsize=size or _SIZE)
    if len(frame):
        axes.fill_between(
            frame["시각"],
            frame["순부하(kW)"],
            frame["원부하(kW)"],
            color=chart_palette().fill,
            # **띠는 선과 같은 색이라 옅어야 한다** (36세션 4절). 진하게 두면
            # 알파가 섞여 원부하 선과 거의 같은 색이 되어 셋이 갈리지 않는다.
            alpha=chart_palette().fill_alpha,
            label="저감분",
        )
    axes.plot(frame["시각"], frame["원부하(kW)"], label="원부하", color=_series()[1], linewidth=1.4)
    axes.plot(frame["시각"], frame["순부하(kW)"], label="순부하", color=_series()[0], linewidth=1.6)
    axes.axhline(
        dispatch.target_kw,
        color=chart_palette().highlight,
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
    time_axis(axes)
    axes.tick_params(axis="x", rotation=45, labelsize=8)
    add_legend(axes)
    return render_png(figure)


def surplus_daily_png(
    usage: UsageData, surplus_kw: pd.Series, *, size: tuple[float, float] | None = None
) -> bytes:
    """연간 일별 잉여량 (3장 · 7.7). **주말에 몰리는지**가 보여야 한다."""
    apply_style()
    frame = surplus_daily_frame(usage, surplus_kw)
    figure, axes = plt.subplots(figsize=size or _SIZE)
    palette = {"평일": _series()[0], "토요일": _series()[3], "일요일": _series()[2]}
    for kind, group in frame.groupby("구분", sort=False):
        axes.bar(
            group["날짜"],
            group["잉여(kWh)"],
            label=str(kind),
            color=palette.get(str(kind), _series()[0]),
            width=1.0,
        )
    axes.set_ylabel("일별 잉여 (kWh)")
    date_axis(axes)
    axes.tick_params(axis="x", rotation=45, labelsize=8)
    add_legend(axes)
    return render_png(figure)


# ===================================================================== 36세션 2절 · 슬라이드 차트
#
# PPT 가 새로 쓰는 그림 셋이다. **화면과 같은 프레임을 본다**
# (:mod:`kwise.report.frames`) — 각자 만들면 같은 이름의 그림이 서로 다른 수를
# 그리고, 그 어긋남은 나란히 놓기 전까지 보이지 않는다 (13세션).


def daily_usage_png(usage: UsageData, *, size: tuple[float, float] | None = None) -> bytes:
    """연간 일별 사용량 (슬라이드 「전력사용현황」).

    **기온을 곁들이지 않는다.** 화면의 기온 그래프는 지역을 골라야 나오는데,
    슬라이드는 그 입력 없이도 한 장이 채워져야 한다. 여기서 볼 것은 계절에 따라
    사용량이 어떻게 오르내리는가 하나다 — 한 장에 한 메시지다 (36세션 5절).
    """
    apply_style()
    marks = chart_palette()
    frame = daily_usage_frame(usage)
    figure, axes = plt.subplots(figsize=size or _SIZE)
    axes.fill_between(
        frame["날짜"],
        0.0,
        frame["사용량(kWh)"] / 1000.0,
        color=marks.fill,
        alpha=marks.fill_alpha,
    )
    axes.plot(frame["날짜"], frame["사용량(kWh)"] / 1000.0, color=marks.fill, linewidth=1.0)
    axes.set_ylabel("일 사용량 (MWh)")
    date_axis(axes)
    axes.tick_params(axis="x", rotation=45, labelsize=8)
    return render_png(figure)


def daily_temperature_png(
    usage: UsageData, temperature: pd.Series, *, size: tuple[float, float] | None = None
) -> bytes:
    """일별 사용량과 일평균 기온을 겹친 한 장 (38세션 · 화면 「부하 패턴」).

    **냉난방이 부하의 얼마를 차지하는지**를 눈으로 재는 그림이다 (30세션 4절).
    여름·겨울에 두 선이 함께 솟으면 냉난방 부하가 크고, 기온이 오르내려도
    사용량이 평평하면 공정 부하다 — 태양광·ESS 판단이 갈리는 자리다.

    **평균 기준선을 함께 긋는다** (32세션 1절). 고온·저온이 부하를 미는 크기는
    평균과의 거리로 읽히므로, 선이 없으면 곡선이 어느 쪽으로 얼마나 벗어난
    것인지 눈금을 세어야 한다. 이름은 관측 기간이 정한다 (연평균 / 기간 평균).

    **기온 축은 오른쪽이다.** 단위가 다른 둘을 한 축에 얹으면 사용량 축의
    범위가 기온에 끌려가 하루치 차이가 뭉개진다 — 화면과 같은 규약이다.
    """
    apply_style()
    marks = chart_palette()
    frame = daily_temperature_frame(usage, temperature)
    figure, axes = plt.subplots(figsize=size or _SIZE)
    load_color, temp_color = marks.series[0], marks.increase
    axes.plot(
        frame["날짜"],
        frame["사용량(kWh)"] / 1000.0,
        color=load_color,
        linewidth=1.0,
        label="일 사용량",
    )
    axes.set_ylabel("일 사용량 (MWh)", color=load_color)
    date_axis(axes)
    axes.tick_params(axis="x", rotation=45, labelsize=8)

    right = axes.twinx()
    right.plot(
        frame["날짜"],
        frame["일평균 기온(℃)"],
        color=temp_color,
        linewidth=1.0,
        label="일평균 기온",
    )
    right.set_ylabel("일평균 기온 (℃)", color=temp_color)
    right.grid(visible=False)
    if len(frame):
        mean = temperature_mean_frame(frame)
        value = float(mean["평균 기온(℃)"].iloc[0])
        right.axhline(value, color=temp_color, linestyle="--", linewidth=1.0, alpha=0.7)
        # **값을 선 위 라벨로 적는다** (32세션 1절). 범례를 늘리지 않는다.
        #
        # **바탕을 깔아 준다.** 사용량 곡선이 이 높이를 지나가면 글자가 선 위에
        # 겹쳐 읽히지 않는다 — 흰 캔버스가 확정이라(36세션 4절) 같은 색으로
        # 덮으면 된다. 화면(altair)에서는 tooltip 이 있어 필요 없던 장치다.
        right.annotate(
            str(mean["기준선"].iloc[0]),
            xy=(0.008, value),
            xycoords=("axes fraction", "data"),
            xytext=(0, 3),
            textcoords="offset points",
            fontsize=8,
            color=temp_color,
            bbox={"facecolor": marks.canvas, "edgecolor": "none", "pad": 1.0, "alpha": 0.85},
        )
    # **범례를 두 축에서 모아 하나로 단다.** twinx 는 축마다 따로 그려서
    # 그대로 두면 상자가 둘 겹친다.
    handles = axes.get_lines() + right.get_lines()[:1]
    add_legend(axes, handles=handles, labels=[line.get_label() for line in handles])
    return render_png(figure)


def power_factor_day_png(
    usage: UsageData,
    day: RepresentativeDay,
    *,
    current_pct: float,
    target_pct: float,
    size: tuple[float, float] | None = None,
) -> bytes:
    """대표일 부하와 **역률 판정 창** (38세션 · 화면 7.4 의 둘째 그림).

    **역률은 시각마다 재지 않는다** — 무효전력 실측이 없다. 주간(08~22시)만
    지상역률 판정 대상이므로 그 구간에 표식을 얹어 **어디가 요금 대상인지**를
    보인다. 곡선이 아니라 창을 보여 주는 그림이다 (15세션 2-3).
    """
    apply_style()
    marks = chart_palette()
    frame = power_factor_day_frame(usage, day.date, current_pct=current_pct, target_pct=target_pct)
    figure, axes = plt.subplots(figsize=size or _SIZE)
    if len(frame):
        axes.plot(
            frame["시각"],
            frame["부하(kW)"],
            color=marks.series[0],
            linewidth=1.6,
            label="15분 부하",
        )
        window = frame[frame["구간"].str.startswith("주간")]
        if len(window):
            axes.scatter(
                window["시각"],
                window["부하(kW)"],
                s=12,
                color=marks.highlight,
                alpha=0.45,
                label="지상역률 판정 구간",
            )
    axes.set_ylabel("부하 (kW)")
    axes.set_xlabel(f"{day.title} · 15분")
    time_axis(axes)
    axes.tick_params(axis="x", rotation=45, labelsize=8)
    add_legend(axes)
    return render_png(figure)


def monthly_charge_png(
    structure: ChargeStructure, *, size: tuple[float, float] | None = None
) -> bytes:
    """월별 요금 구성 — 기본요금 + 계시별 전력량요금 **누적 막대**.

    **화면과 같은 그림이다** (27세션 3-2). 밑단(기본요금)이 같은 높이로 이어지고
    그 위 세 조각만 계절따라 움직이는 것이 이 요금제의 모습이다.

    **막대는 자르지 않는다** (17세션 0절). 길이가 곧 금액이다.
    """
    apply_style()
    marks = chart_palette()
    frame = monthly_charge_frame(structure)
    months = list(dict.fromkeys(frame["월"]))
    labels = month_labels(months)
    positions = np.arange(len(months), dtype=float)
    parts = {"기본요금": marks.base_fee} | marks.band

    figure, axes = plt.subplots(figsize=size or _SIZE)
    bottom = np.zeros(len(months))
    for part, color in parts.items():
        values = np.array(
            [
                float(frame[(frame["월"] == month) & (frame["구분"] == part)]["원"].sum()) / 1e8
                for month in months
            ]
        )
        axes.bar(positions, values, width=0.62, bottom=bottom, label=part, color=color)
        bottom += values
    axes.set_xticks(list(positions))
    axes.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
    axes.set_ylabel("요금 (억원)")
    add_legend(axes)
    return render_png(figure)


#: 도넛 격자의 칸 (36세션 4절). **넷을 2×2 로** 한 장에 넣는다.
DONUT_GRID = (2, 2)


def band_donut_grid_png(
    structure: ChargeStructure,
    seasons: Sequence[tuple[str, str | None]],
    *,
    size: tuple[float, float] = (7.4, 5.6),
) -> bytes:
    """계시별 사용량 구성 도넛 **넷을 2×2 격자로** (36세션 4절).

    화면은 넷을 한 줄에 두지만 슬라이드는 폭이 아니라 **넓이**가 남는다 —
    한 줄로 늘어놓으면 도넛 하나가 폭의 4분의 1 이라 조각 라벨이 읽히지 않는다.

    **넷의 조각 순서·색·시작각을 맞춘다.** 도넛에는 y 축이 없으므로 축 대신
    이 셋이 「같은 잣대로 그렸다」 를 진다 — 하나라도 어긋나면 계절 사이의
    차이가 아니라 그림의 차이를 보게 된다.

    **비중은 그 계절 안에서 다시 잰다** (30세션 5-2). 전체 대비로 두면 네 원이
    각각 일부만 칠해져 무엇의 구성인지 알 수 없다.

    Args:
        seasons: ``(이름, 계절키)`` 차례. 화면의 갈래와 같은 것을 넘긴다.
    """
    apply_style()
    marks = chart_palette()
    rows, cols = DONUT_GRID
    figure, grid = plt.subplots(rows, cols, figsize=size)
    axes_list = list(np.asarray(grid).ravel())
    order = list(BAND_LABELS.values())

    for axes, (label, key) in zip(axes_list, list(seasons)[: len(axes_list)], strict=False):
        frame = band_frame(structure, season=key)
        axes.set_axis_off()
        if frame.empty:
            continue
        # **순서를 고정한다.** 자료 순서에 맡기면 계절마다 색이 돈다.
        frame = frame.set_index("시간대").reindex(order).dropna(subset=["사용량(kWh)"])
        axes.pie(
            frame["사용량(kWh)"].astype(float),
            labels=[str(text) for text in frame["라벨"]],
            colors=[marks.band[str(name)] for name in frame.index],
            startangle=90,
            counterclock=False,
            wedgeprops={"width": 0.42, "edgecolor": marks.canvas, "linewidth": 1.0},
            textprops={"fontsize": 9, "color": marks.text},
            labeldistance=1.18,
        )
        total = float(frame["사용량(kWh)"].sum())
        axes.set_title(f"{label} · {total / 1000:,.0f} MWh", fontsize=11, color=marks.text)

    # 계절이 넷에 못 미치면 남는 칸은 비운다. 빈 원을 그리면 「안 썼다」 로 읽힌다.
    for axes in axes_list[len(list(seasons)) :]:
        axes.set_axis_off()
    return render_png(figure)
