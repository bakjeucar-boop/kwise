r"""매뉴얼 앵커 (요구사항서 10.2 — 화면과 매뉴얼의 역할 분담).

**화면에 설명을 길게 넣지 않는다.** 판단 기준은 하나다.

    화면에 둘 것     없으면 입력을 못 하거나 결과를 오독하는 것
    매뉴얼로 보낼 것  출처·근거 조문·제도 설명·원리·배경

형태는 **화면에 한 줄 + 정보 표시(ⓘ) 툴팁**이다 (16세션 4절).

    바꾸기 전   화면 한 줄 + 「자세히」 링크가 정적 사본의 앵커로
    바꾼 뒤     화면 한 줄 + ⓘ 에 얹힌 요지

**화면에서 링크를 걷어냈다.** 새 창으로 나가면 하던 입력을 잃고, 매뉴얼이
없으면 죽은 링크가 되며, 정적 사본을 두는 자리가 실행 방식마다 달라 404 가
났다 (12세션에 겪었다). 요지는 툴팁에 얹으면 자리를 옮기지 않고 읽힌다.

**앵커 목록은 남긴다.** 무엇을 화면에 두지 않기로 했는지가 여기 적혀 있고,
매뉴얼(``docs\MANUAL.md``)이 이 목록을 그대로 ``id`` 로 쓴다. 목록은
``docs\MANUAL_ANCHORS.md`` 로도 내보내며 (``tools\export_manual_anchors.py``),
두 벌이 어긋나지 않는지 테스트가 지킨다.

**예외 — 경고는 화면에 남긴다.** 역률 미달, 고출력 셀 사양, 결측 편중, 계약전력
변경 위험처럼 결과 해석을 바꾸는 것은 툴팁으로 보내도 안 읽는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from kwise.ui.labels import measure_title
from kwise.ui.text import markdown_safe

__all__ = [
    "ANCHORS",
    "ANCHOR_DOC_FILENAME",
    "MANUAL_FILENAME",
    "ManualAnchor",
    "anchor",
    "anchor_document",
    "anchor_keys",
    "manual_path",
    "manual_tip",
]

ANCHOR_DOC_FILENAME = "MANUAL_ANCHORS.md"

MANUAL_FILENAME = "MANUAL.html"


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def manual_path(docs_dir: Path | None = None) -> Path:
    r"""매뉴얼 원본 경로. 기본은 ``<프로젝트>\docs\MANUAL.html``.

    **화면은 이 파일을 열지 않는다** (16세션 4절). 문서를 만들고 검사하는
    도구만 쓴다 — 화면에서 링크를 걷어냈기 때문이다.
    """
    base = docs_dir if docs_dir is not None else _project_root() / "docs"
    return base / MANUAL_FILENAME


@dataclass(frozen=True)
class ManualAnchor:
    """매뉴얼 한 꼭지.

    Attributes:
        key: HTML ``id``. 매뉴얼 안의 자리다.
        title: 매뉴얼 소제목이자 화면 툴팁의 첫머리.
        origin: **화면 어디에서 이 요지가 필요한가.** 문서를 쓸 때 순서를 잡는 축이다.
        covers: 매뉴얼이 담을 것. 화면에 두지 않기로 한 내용이 여기 적힌다.
    """

    key: str
    title: str
    origin: str
    covers: str

    @property
    def tip(self) -> str:
        """화면 ⓘ 에 얹을 글. **제목과 요지를 한 덩이로** 준다."""
        return f"{self.title} — {self.covers}"


# 순서는 화면 흐름을 따른다 — 1단계 → 2단계 → 3단계 → 기준 데이터 → 공통.
ANCHORS: tuple[ManualAnchor, ...] = (
    # ---------------------------------------------------------- 1단계 · 진단
    ManualAnchor(
        "column-detection",
        "검침일·전력량 열 판정",
        "1단계 · 데이터 품질",
        "세 단(헤더 행 → 이름 → 값 패턴) 판정 방식, 인코딩 시도 순서, 판정이 "
        "빗나가는 양식의 예. 화면에는 판정 결과와 고치는 드롭다운만 둔다.",
    ),
    ManualAnchor(
        "data-quality",
        "데이터 품질 검사",
        "1단계 · 데이터 품질",
        "결측 미보간 원칙, 정전 판정 흔적 3종 중 2종 규칙, 편중 배수 산정, "
        "그리드 이탈 행의 처리. 화면에는 결측률·정전 건수·경고만 둔다.",
    ),
    ManualAnchor(
        "label-convention",
        "검침 라벨 규약 (구간 끝)",
        "1단계 · 데이터 품질",
        "라벨이 구간 끝이라 하루가 00:15 에 시작해 24:00 에 끝나는 이유, 계절·"
        "시간대 귀속을 한 구간 뺀 시각으로 판정하는 이유, PV 정렬과의 관계.",
    ),
    ManualAnchor(
        "contract-info",
        "계약 정보 넷",
        "1단계 · 계약 정보",
        "계약종별·전압구분·선택요금의 제도적 의미, 갑/을 임계(300·1,000 kW)의 "
        "근거, 연간 이용시간으로 선택요금을 가늠하는 방법. **화면은 청구서 "
        "기재값만 받는다** — 추정치를 미리 채우지 않는다.",
    ),
    ManualAnchor(
        "improvement-summary",
        "1단계가 내는 것과 내지 않는 것",
        "1단계 · 머리말",
        "1단계는 진단만 하고 금액은 2단계에서 낸다 (16세션). 옛 '투자 없이 가능한 "
        "절감액' 이 선택요금 전환·계약전력 조정 카드의 어디로 갔는지, 태양광 기여 "
        "가능성 등급의 판정 모집단이 요금적용전력 대상 슬롯인 이유.",
    ),
    ManualAnchor(
        "load-pattern",
        "부하 패턴 지표",
        "1단계 · 부하 패턴",
        "부하율·기저부하 비율·주말 비율·운영시간 외 부하의 정의와 읽는 법. "
        "운영 시간대를 어디에 쓰는지도 여기 있다.",
    ),
    ManualAnchor(
        "peak-profile",
        "피크 특성과 상위 100구간",
        "1단계 · 피크 특성",
        "월별 최대수요를 12개로 내는 이유, 상위 100구간 시각 분포를 두 벌"
        "(전 슬롯·요금적용전력 대상)로 내는 이유, 발생 시각을 라벨로 보고하는 규약.",
    ),
    ManualAnchor(
        "billing-demand",
        "요금적용전력 3규칙",
        "1단계 · 피크 특성",
        "①경부하 제외 ②대상월(7·8·9·12·1·2) 한정 ③계약전력 하한. 세 규칙이 "
        "태양광에 반대 방향으로 작용하는 까닭과 부하 형태별 지배 관계.",
    ),
    ManualAnchor(
        "charge-structure",
        "현재 요금 구조",
        "1단계 · 요금 구조",
        "기본요금과 전력량요금의 결정 구조, 시간대·계절 구분의 근거, "
        "부분 월 기본요금 처리(merge)와 '연간' 대신 기간으로 적는 이유.",
    ),
    ManualAnchor(
        "contract-adequacy",
        "계약전력 적정성",
        "2단계 · 7.2 계약전력 조정 카드",
        "요금적용전력 하한이 판정을 가르는 이치, 목표 계약전력이 가장 작은 달 ÷ "
        "하한비율인 까닭, 하한 규정을 모를 때 금액을 내지 않는 이유. "
        "화면에는 판정을 가르는 세 수와 **변경 위험 경고**를 둔다 — "
        "1단계가 아니라 바꿀지 말지를 정하는 카드 안이다 (16세션).",
    ),
    # ---------------------------------------------------------- 2단계 · 개선 수단
    ManualAnchor(
        "measure-tariff-switch",
        "7.1 선택요금 전환",
        "2단계 · 선택요금 전환 카드",
        "선택Ⅰ·Ⅱ·Ⅲ 의 설계 의도, 같은 계약종별·전압 안에서만 비교하는 이유, "
        "전 조합을 다시 계산하는 이유.",
    ),
    ManualAnchor(
        "measure-contract",
        "7.2 계약전력 조정",
        "2단계 · 계약전력 조정 카드",
        "기본요금 기준(최대수요전력계가 서는가 — 종별과 전압이 함께 정한다), "
        "하한 비율 30%(종별로 갈리지 않는다 — 15% 는 초·중·고교·유치원 신청 "
        "특례다), 초과사용부가금과 위약 구조.",
    ),
    ManualAnchor(
        "measure-dr",
        "7.3 경제성DR",
        "2단계 · 경제성DR 카드",
        "전력시장운영규칙 제12장, 신뢰성DR 과의 차이, 거래일 제약(토·일·공휴일 "
        "제외), CBL 산정이 전력거래소 몫인 점, 정산 단가를 만들지 않는 이유.",
    ),
    ManualAnchor(
        "measure-power-factor",
        "7.4 역률 개선",
        "2단계 · 역률 개선 카드",
        "약관 제41–43조, 기준 92%·감액 상한 97%, 판정 창 08–22시, 야간 진상 "
        "조항과 지상 간주 100%, 무효전력 실측이 없는 한계.",
    ),
    ManualAnchor(
        "measure-solar",
        "7.5 태양광",
        "2단계 · 태양광 카드",
        "용량 곡선을 20단계로 훑는 이유, 자가소비·잉여의 정의, 발전량 예측의 "
        "R² 와 피크 과소 산출 경향, 일몰 절단과 반 칸 정렬 규약.",
    ),
    ManualAnchor(
        "pv-density",
        "설치 밀도 프리셋",
        "2단계 · 태양광 카드",
        "밀도 하나가 GCR 과 경사각을 함께 정하는 실무 배경, 면적→용량 환산식"
        "(면적 × GCR ÷ 5)의 근거. 화면에는 3지선다·상충 한 줄·환산 용량만 둔다.",
    ),
    ManualAnchor(
        "pv-cost",
        "태양광 투자비",
        "2단계 · 태양광 카드",
        "kWp당 단가로 통일한 이유, 참고단가를 제공하지 않는 이유(인용할 공개 "
        "자료 미확보), 규모의 경제 미반영. 화면에는 미입력 시 **사유**를 둔다.",
    ),
    ManualAnchor(
        "measure-ess",
        "7.6 ESS",
        "2단계 · ESS 카드",
        "목표 요금적용전력에서 출력·용량을 역산하는 방식, 용량 기준을 '하루 최대 "
        "초과 에너지' 로 잡는 이유, 규칙기반 디스패치의 한계, 차익거래를 "
        "합산하지 않는 이유.",
    ),
    ManualAnchor(
        "ess-cost-reference",
        "ESS 참고단가",
        "2단계 · ESS 카드",
        "에너지경제연구원 LCOS 연구 인용, 참고단가가 **하한선**인 이유(전용실·"
        "소화설비·연계공사 제외), 자동 적용하지 않는 이유. 화면에는 방전시간과 "
        "환산단가 한 줄만 둔다.",
    ),
    ManualAnchor(
        "measure-surplus",
        "잉여 처리",
        "2단계 · 태양광 카드 · 잉여 처리",
        # **41세션에 자리를 옮겼다.** 개선안 7.7 을 없애고 태양광 카드 안으로
        # 넣었으므로 앵커가 가리키는 자리도 함께 옮긴다.
        "상계거래와 외부 판매의 제도 차이, 상계 구간(10 kW·1,000 kW)과 인버터를 "
        "입력받지 않는 이유, 당월 차감과 이월, 자격요건을 판정하지 않는 이유, "
        "단가를 지어내지 않는 이유.",
    ),
    ManualAnchor(
        "weather-source",
        "기상 자료와 지역 선택",
        "2단계 · 태양광 카드",
        "Open-Meteo ERA5 출처와 라이선스, 0.25° 격자와 시군구 선택이 정확도를 "
        "희생하지 않는 이유, 사전 취득분 폴백 순서, 인접 격자로 대체하지 않는 원칙.",
    ),
    # ---------------------------------------------------------- 3단계 · 비교
    ManualAnchor(
        "combination",
        "조합 비교",
        "3단계 · 조합 비교",
        # 확실성 이야기는 뺐다 (28세션 4절) — 화면에 없는 것을 화면 툴팁이
        # 가리키고 있었다. 매뉴얼 본문에는 그대로 있다.
        "조합마다 요금을 다시 계산하는 이유(단순 합산이 틀리는 구조), 수단을 "
        "쌓는 순서가 결과에 미치는 영향.",
    ),
    # **확실성 등급과 감도 앵커를 뺐다** (28세션 4·5절). 둘 다 화면에서 없앴으므로
    # 가리킬 자리가 없다 — 놀고 있는 앵커를 두지 않는다 (22세션 5절). 매뉴얼의
    # 두 절(`#certainty`·`#sensitivity`)은 그대로다. **53세션에 산출물에서도 뺐다.**
    # Excel·Word 에는 확실성 등급과
    # 감도 표가 남아 있고, 그것을 읽는 사람이 갈 자리이기 때문이다.
    ManualAnchor(
        "payback",
        "회수기간",
        "3단계 · 조합 비교",
        "단순 회수기간의 정의, OPEX·열화·교체비 미반영, 투자비를 모를 때 "
        "0원이 아니라 미산출로 두는 이유.",
    ),
    ManualAnchor(
        "excel-report",
        "Excel 산출물",
        "3단계 · 내려받기",
        "아홉 시트의 구성과 읽는 순서, 감도 시트와 감도 상세의 분리, 파일명 접미사 규약.",
    ),
    # **Word 앵커를 두지 않는다** (36세션 1절). 화면에서 단추를 감췄으므로 가리킬
    # 자리가 없다 — 놀고 있는 앵커를 두지 않는 규약이다 (22세션 5절). 매뉴얼의
    # Word 절은 그대로 남는다. 되살릴 때 무엇이 들어 있었는지가 거기 적혀 있다.
    ManualAnchor(
        "ppt-report",
        "PPT 보고서",
        "3단계 · 내려받기",
        "슬라이드 차례와 한 장에 담는 것, 켠 수단만 한 장씩 들어가는 규칙, "
        "부록에 산출 근거만 싣는 이유.",
    ),
    # ---------------------------------------------------------- 기준 데이터
    ManualAnchor(
        "rules-admin",
        "기준 데이터 관리",
        "기준 데이터 화면",
        "법령 유래(rules_kr.json)와 판단값(assumptions.json)을 가르는 이유, "
        "코드에 기본값을 두지 않는 원칙, 엑셀 왕복 보조 경로.",
    ),
    ManualAnchor(
        "rules-restore",
        "원복 세 경로",
        "기준 데이터 화면",
        "직전/출고/항목별 원복의 차이와 백업 세 층(defaults·data·backup), "
        "출고 복원 전 미리보기를 강제하는 이유, 손상 복구 알림.",
    ),
    ManualAnchor(
        "rules-expiry",
        "만료 감지와 갱신 절차",
        "기준 데이터 화면",
        "임계(요금 12·약관 24·참고단가 24개월)의 근거, 자동 수집을 하지 않는 "
        "이유, 원문 확인처와 갱신 절차.",
    ),
    ManualAnchor(
        "weather-archive",
        "기상 사전 취득 현황",
        "기준 데이터 화면",
        "격자 × 연도 저장 구조, 부분 취득이 정상 상태인 이유, 추가 취득 방법"
        "(tools\\fetch_weather.py).",
    ),
    # ---------------------------------------------------------- 공통
    ManualAnchor(
        "not-included",
        "미포함 요금요소",
        "모든 화면 · 각주",
        "기본요금과 전력량요금만 계산하는 범위, 기후환경요금·연료비조정액·"
        "부가가치세·전력기금이 빠져 실제 절감액이 더 큰 이유.",
    ),
    ManualAnchor(
        "known-limits",
        "알려진 한계",
        "모든 화면 · 각주",
        "부록 D 전문. 인증·신고용이 아니라는 점과 각 한계의 배경.",
    ),
)


def anchor_keys() -> tuple[str, ...]:
    return tuple(item.key for item in ANCHORS)


_BY_KEY: dict[str, ManualAnchor] = {item.key: item for item in ANCHORS}


def anchor(key: str) -> ManualAnchor:
    """앵커 하나. **없는 키를 쓰면 바로 실패한다** — 죽은 링크를 화면에 내지 않는다."""
    try:
        return _BY_KEY[key]
    except KeyError as exc:
        raise KeyError(f"등록되지 않은 매뉴얼 앵커입니다: {key!r}") from exc


def manual_tip(key: str) -> str:
    """화면 요소의 ``help=`` 에 넣을 글 (16세션 4절).

    **없는 키를 쓰면 바로 실패한다** — 빈 툴팁을 화면에 내지 않는다.
    **escape 해서 낸다** (25세션 2절) — ``help=`` 도 마크다운을 해석한다.
    **개선안 번호는 화면 순번으로 바꿔 낸다** (27세션 2절) — 매뉴얼 소제목
    (:attr:`ManualAnchor.title`)은 절 번호를 그대로 쓰고, 화면에 실을 때만
    :func:`~kwise.ui.labels.measure_title` 을 지난다.
    """
    item = anchor(key)
    return markdown_safe(f"{measure_title(item.title)} — {item.covers}")


_DOC_HEADER = f"""# 매뉴얼 앵커 목록

`docs\\{MANUAL_FILENAME}` 을 쓸 때 **이 표의 `id` 를 그대로 쓴다.** 화면의
[자세히] 링크가 `{MANUAL_FILENAME}#<id>` 로 걸려 있으므로, 이름이 맞으면 문서를
만드는 순간 링크가 살아난다. 매뉴얼이 없는 동안 링크는 비활성으로 그려진다.

정본은 `src\\kwise\\ui\\anchors.py` 다. **이 파일은 거기서 생성한다** —
`tools\\export_manual_anchors.py` 를 돌리고, 두 벌이 어긋나면 테스트가 깨진다.

화면에 둘 것과 매뉴얼로 보낼 것의 판단 기준은 하나다.

| | 기준 |
|---|---|
| 화면에 둘 것 | 없으면 입력을 못 하거나 결과를 오독하는 것 |
| 매뉴얼로 보낼 것 | 출처·근거 조문·제도 설명·원리·배경 |

**예외 — 경고는 화면에 남긴다.** 역률 미달, 고출력 셀 사양, 결측 편중,
계약전력 변경 위험처럼 결과 해석을 바꾸는 것은 툴팁으로 보내도 안 읽는다.
"""


def anchor_document() -> str:
    """``docs\\MANUAL_ANCHORS.md`` 전문.

    **정본은 :data:`ANCHORS` 이고 문서는 생성물이다.** 손으로 고치면 어긋나므로
    테스트가 두 벌을 대조한다.
    """
    lines = [
        _DOC_HEADER,
        f"전체 {len(ANCHORS)}개.",
        "",
        "| # | id | 제목 | 화면 위치 | 매뉴얼이 담을 것 |",
        "|---|---|---|---|---|",
    ]
    for index, item in enumerate(ANCHORS, start=1):
        covers = item.covers.replace("|", "\\|")
        lines.append(f"| {index} | `{item.key}` | {item.title} | {item.origin} | {covers} |")
    lines.append("")
    return "\n".join(lines)
