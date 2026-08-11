"""기준 데이터 관리 화면 (요구사항서 12장·10.1).

**근거를 값 옆에 항상 보이게 두는 것이 이 화면의 존재 이유다.** 그래서 항목마다
한 화면에 함께 보여 주고 **접어두지 않는다.**

    구분 배지 [법령]/[판단] · 라벨 · 값(편집) · 출고값(다를 때만, 되돌리기) ·
    근거(조문과 시행일 또는 판단 근거) · 확인일과 경과 개월 · [확인함] · 비고

원복 세 경로(직전 / 출고 / 항목별)를 모두 노출한다. **출고 복원은 실행 전에 차이
목록을 보여 주고 확인받는다** — ``confirmed=False`` 로 먼저 부른다.

API 는 지난 세션이 다 만들어 두었다. **이 화면은 호출만 한다.**
"""

from __future__ import annotations

import streamlit as st

from kwise.pv.archive import archive_status
from kwise.rules import (
    EditResult,
    RuleOrigin,
    assumptions,
    confirm,
    describe_items,
    diff_from_defaults,
    expiry_warnings,
    read_history,
    restore_defaults,
    restore_item,
    restore_previous,
    rules,
    set_value,
)
from kwise.tariff import load_tariff
from kwise.ui.anchors import detail_suffix
from kwise.ui.cache import apply_rule_edit
from kwise.ui.rules_view import (
    RuleRow,
    build_rows,
    count_rows,
    diff_frame,
    header_text,
    weather_panel,
)

__all__ = ["render", "render_alerts"]

_CONFIRM_KEY = "rules_restore_confirm"


def render_alerts() -> None:
    """모든 화면 위에 거는 알림.

    **손상 복구를 조용히 넘기지 않는다** — 갱신한 값으로 계산되는 줄 알고 결과를
    쓰게 된다. 만료 경고도 시작 화면에서 함께 본다.
    """
    for ruleset in (rules(), assumptions()):
        if ruleset.recovered_from:
            st.error(
                f"기준 데이터 **{ruleset.origin.filename}** 를 읽지 못해 "
                f"`{ruleset.recovered_from}` 에서 복구했습니다. "
                "값이 최신인지 「기준 데이터」 화면에서 확인하십시오."
            )
    warnings = expiry_warnings()
    if warnings:
        with st.expander(f"⚠ 기준 데이터 확인 필요 {len(warnings)}건", expanded=False):
            for warning in warnings:
                st.write(f"- {warning.message()}")


def render() -> None:
    st.header("기준 데이터")
    st.caption(
        "법령 유래 값과 우리 판단값을 갈라 둡니다. 법령 항목은 함부로 고치지 말고, "
        "판단값은 조정하라고 있는 것입니다. " + detail_suffix("rules-admin")
    )

    warnings = expiry_warnings(include_weather=False)
    rows = build_rows(describe_items(), warnings)
    counts = count_rows(rows)
    st.markdown(f"### {header_text(counts)}")

    _tariff_block()
    _restore_block()
    st.divider()

    for row in rows:
        _row(row)

    st.divider()
    _weather_block()
    _history_block()


# --------------------------------------------------------------------- 요금표


def _tariff_block() -> None:
    """요금표 검증 여부. **이 화면에만 둔다.**

    옆단과 진단 화면에도 걸어 두었더니 매 화면에서 같은 문장을 읽게 되었다.
    쓰는 사람이 손댈 곳은 여기 하나이므로 여기서만 밝힌다 (12세션).
    """
    table = load_tariff()
    with st.container(border=True):
        st.markdown(f"**요금표** — {table.source or '출처 미기재'} ({table.effective_date} 시행)")
        if table.verified:
            st.caption("실제 청구서와 대조해 확인했습니다.")
        else:
            st.caption(
                "아직 실제 청구서와 대조하지 않았습니다. 단가는 공표 자료 그대로이며, "
                "청구서로 한 번 맞춰 보면 확신이 올라갑니다."
            )


# --------------------------------------------------------------------- 항목 한 줄


def _row(row: RuleRow) -> None:
    badge = "🟥 법령" if row.is_statutory else "🟦 판단"
    flags = []
    if row.needs_check:
        flags.append("⚠ 확인 필요")
    if row.changed:
        flags.append("● 출고값과 다름")

    with st.container(border=True):
        head, edit = st.columns([3, 2])
        with head:
            st.markdown(f"**{badge}** · {row.view.label}  \n`{row.key}`")
            if flags:
                st.caption(" · ".join(flags))
            # **근거를 값 옆에 둔다.** 원문 확인처는 근거 줄 옆이다.
            source = row.source_text
            if row.link:
                source += f" · [원문 확인]({_url(row.link)})"
            st.caption(source)
            st.caption(f"확인일 — {row.verified_text}")
            if row.view.note:
                st.caption(f"비고 — {row.view.note}")

        with edit:
            _editor(row)


def _editor(row: RuleRow) -> None:
    """값 편집 위젯.

    **형을 지켜서 되돌려 준다.** 정수 항목(대상월·판정 창)을 실수로 저장하면
    스키마 검증에 걸리거나, 걸리지 않더라도 산출물에 ``12.0`` 으로 찍힌다.
    """
    value = row.view.value
    widget_key = f"rule_edit_{row.key}"
    stored: bool | int | float | str | None = None

    if isinstance(value, bool):
        stored = st.checkbox("값", value=value, key=widget_key)
    elif isinstance(value, int):
        stored = int(st.number_input("값", value=value, step=1, key=widget_key))
    elif isinstance(value, float):
        stored = float(st.number_input("값", value=value, key=widget_key, format="%.6g"))
    elif isinstance(value, str):
        stored = st.text_input("값", value=value, key=widget_key)
    else:
        # 목록·사전은 화면에서 고치지 않는다. 엑셀 왕복 경로를 쓴다 (12.2).
        st.code(repr(value), language="python")
        st.caption("구조가 있는 값은 tools\\export_rules_xlsx.py 로 고칩니다.")

    buttons = st.columns(3)
    if stored is not None and buttons[0].button("저장", key=f"save_{row.key}"):
        _report(apply_rule_edit(set_value(row.key, stored)))
    if buttons[1].button(
        "확인함", key=f"confirm_{row.key}", help="값은 그대로 두고 확인일만 갱신합니다."
    ):
        _report(apply_rule_edit(confirm(row.key)))
    if row.changed and buttons[2].button(
        "되돌리기", key=f"revert_{row.key}", help=f"출고값 {row.view.default_value!r} 로"
    ):
        _report(apply_rule_edit(restore_item(row.key)))
    if row.changed:
        st.caption(f"출고값 — `{row.view.default_value!r}`")


def _report(result: EditResult) -> None:
    if result.ok:
        st.success(result.message)
        st.rerun()
    else:
        st.error(result.message)
        for issue in result.issues:
            st.write(f"- {issue}")


# --------------------------------------------------------------------- 원복 세 경로


def _restore_block() -> None:
    st.markdown("#### 원복")
    st.caption(
        "직전 상태 · 출고 상태 · 항목별 셋을 모두 씁니다. 항목별 되돌리기는 각 줄에 "
        "있습니다. " + detail_suffix("rules-restore")
    )
    for origin in (RuleOrigin.STATUTORY, RuleOrigin.JUDGEMENT):
        columns = st.columns([2, 1, 1])
        columns[0].write(f"**{origin}** (`{origin.filename}`)")
        if columns[1].button("직전 상태로", key=f"restore_prev_{origin.name}"):
            _report(apply_rule_edit(restore_previous(origin)))
        if columns[2].button("출고 상태로", key=f"restore_def_{origin.name}"):
            st.session_state[_CONFIRM_KEY] = origin.name
            st.rerun()

    pending = st.session_state.get(_CONFIRM_KEY)
    if pending:
        origin = RuleOrigin[pending]
        # **미리보기 먼저.** confirmed=False 면 실행하지 않고 차이만 돌려준다.
        preview = restore_defaults(origin, confirmed=False)
        diffs = diff_from_defaults(origin)
        st.warning(preview.message)
        if diffs:
            st.dataframe(diff_frame(diffs), hide_index=True, width="stretch")
        columns = st.columns(2)
        if columns[0].button("확인했습니다 — 출고값으로 되돌립니다", type="primary"):
            result = apply_rule_edit(restore_defaults(origin, confirmed=True))
            st.session_state.pop(_CONFIRM_KEY, None)
            _report(result)
        if columns[1].button("취소"):
            st.session_state.pop(_CONFIRM_KEY, None)
            st.rerun()


# --------------------------------------------------------------------- 기상 현황


def _weather_block() -> None:
    st.markdown("#### 기상 데이터 현황")
    panel = weather_panel(archive_status())
    columns = st.columns(4)
    columns[0].metric("확보 격자", f"{panel.cell_count}개")
    columns[1].metric("연도", panel.year_text)
    columns[2].metric("용량", f"{panel.megabytes:,.1f} MB")
    columns[3].metric("최종 취득", panel.fetched_text)
    # **만료 경고를 달지 않는다. 부분 취득은 정상 상태다.**
    st.caption(
        f"필요한 격자만 받아 두는 구조라 일부만 있어도 정상입니다. 없는 격자는 "
        f"조회 시점에 Open-Meteo 에서 받습니다. 저장 위치 — `{panel.root}`. "
        + detail_suffix("weather-archive")
    )


# --------------------------------------------------------------------- 이력


def _history_block() -> None:
    with st.expander("변경 이력", expanded=False):
        records = read_history()
        if not records:
            st.write("아직 변경이 없습니다.")
            return
        st.dataframe(
            [
                {
                    "시각": record.changed_at,
                    "파일": record.file,
                    "항목": record.key,
                    "이전": record.before,
                    "이후": record.after,
                    "동작": record.action,
                    "비고": record.note,
                }
                for record in reversed(records)
            ],
            hide_index=True,
            width="stretch",
        )


def _url(link: str) -> str:
    """확인처 문자열에서 주소만 뽑는다 (``설명 — https://…`` 꼴)."""
    marker = "http"
    index = link.find(marker)
    return link[index:].split()[0] if index >= 0 else link
