"""문서 생성 시험 (요구사항서 13장).

**여기서 지키는 것 다섯**

    ① md 두 개에서 html 두 개가 나온다
    ② html 이 **단일 파일이다** — 바깥 이미지·스타일·스크립트를 참조하지 않는다
    ③ 목차 링크가 실제 절로 연결된다 (죽은 링크가 없다)
    ④ 앵커 30개가 매뉴얼에 모두 있다
    ⑤ 화면의 [자세히] 링크가 살아나고 그 앵커가 매뉴얼과 일치한다
    ⑥ **커밋된 html 이 원본과 같다** (74세션) — 나머지는 임시 폴더에 새로
      만들어 보므로 **만드는 쪽만 지켰다.** 저장소의 것은 아무도 안 봤고,
      그래서 둘이 **S58 판으로 열네 세션**을 서 있었다
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from kwise.docsite import (
    DEFAULT_DOCS,
    TOC_LEVELS,
    build_all,
    build_page,
    collect_anchors,
    render_html,
    render_markdown,
    slugify,
)
from kwise.ui.anchors import ANCHORS, MANUAL_FILENAME, anchor_keys, manual_tip

#: 파일 전체가 기록 묶음이다 (S176 4절) — 새 시험도 저절로 붙는다.
pytestmark = pytest.mark.records

DOCS = Path("docs")
SOURCES = ("TECHNICAL.md", "MANUAL.md")
TARGETS = ("TECHNICAL.html", "MANUAL.html")


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """원본을 임시 폴더로 옮겨 새로 만든다. 저장소 산출물을 건드리지 않는다."""
    workspace = tmp_path_factory.mktemp("docs")
    for name in SOURCES:
        (workspace / name).write_text((DOCS / name).read_text(encoding="utf-8"), encoding="utf-8")
    build_all(workspace)
    return workspace


# ===================================================================== ① 생성


def test_원본_두_개가_있다() -> None:
    for name in SOURCES:
        assert (DOCS / name).is_file(), f"{name} 이 없습니다."


def test_md_두_개에서_html_두_개가_나온다(built: Path) -> None:
    for name in TARGETS:
        target = built / name
        assert target.is_file()
        assert target.stat().st_size > 10_000


def test_저장소에도_생성물이_있다() -> None:
    """``tools\\build_docs.py`` 를 돌린 결과가 커밋되어 있어야 한다."""
    for name in TARGETS:
        assert (DOCS / name).is_file(), f"{name} — tools\\build_docs.py 를 실행하십시오."


def test_저장소의_생성물이_원본과_같다() -> None:
    """**있는지가 아니라 최신인지를 본다** (74세션 1절).

    바로 위 시험은 파일이 **있는지**만 봤다. 그래서 `docs\\*.html` 둘이
    **S58 판으로 열네 세션**을 서 있었는데 아무것도 말하지 않았다 —
    S61·S63·S66 이 원본만 고치고 `build_docs.py` 를 안 돌렸고, S73 이
    다른 일로 그것을 돌리다가 알았다. **결함 유형 ⑤** — 시험은 통과하는데
    실물이 낡아 있었다.

    이 파일의 다른 시험들은 **임시 폴더에 새로 만들어** 본다("저장소 산출물을
    건드리지 않는다"). 곧 **만드는 쪽은 지키는데 커밋된 것은 아무도 안 봤다.**

    **장치는 `test_앵커_문서가_정본과_같다` 를 옮겨 썼다** — 다시 만들어
    전문을 대조하고, 어긋나면 **무엇을 돌려야 하는지** 실패 메시지가 말한다.

    **전문을 그대로 맞대도 된다.** :func:`render_html` 은 원본 글·제목·
    묻어 둔 스타일·스크립트만으로 글을 짓는다 — 생성 시각도, 경로도, 환경도
    안 들어간다. 그래서 같은 원본이면 언제 어디서 돌려도 같은 글이 나온다.
    """
    for source, target, title in DEFAULT_DOCS:
        built_now, _headings = render_html((DOCS / source).read_text(encoding="utf-8"), title=title)
        # **`assert` 로 맞대지 않는다.** 어긋나면 pytest 가 html 전문을 줄줄이
        # 펴는데(심어 보니 577줄), 이 실패는 **손으로 고치는 것이 아니라 도구를
        # 돌려 고치는 것**이라 그 diff 를 아무도 읽지 않는다. 할 일만 낸다.
        if (DOCS / target).read_text(encoding="utf-8") != built_now:
            pytest.fail(
                f"{target} 이 {source} 보다 낡았습니다 — tools\\build_docs.py 를 실행하십시오."
            )


def test_원본이_없으면_만들지_않고_실패한다(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        build_page(tmp_path / "없음.md", tmp_path / "없음.html", "제목")


def test_문서_목록이_둘이다() -> None:
    assert len(DEFAULT_DOCS) == 2
    assert {item[0] for item in DEFAULT_DOCS} == set(SOURCES)


# ===================================================================== ② 단일 파일


@pytest.mark.parametrize("name", TARGETS)
def test_바깥_자원을_참조하지_않는다(built: Path, name: str) -> None:
    """**html 하나만 옮겨도 그대로 열려야 한다.**"""
    text = (built / name).read_text(encoding="utf-8")
    assert "<link" not in text, "외부 스타일시트를 참조합니다."
    assert not re.search(r"<script[^>]+\bsrc=", text), "외부 스크립트를 참조합니다."
    assert not re.search(r'(?:src|href)="(?:https?:)?//', text), "외부 주소를 참조합니다."
    assert "@import" not in text


@pytest.mark.parametrize("name", TARGETS)
def test_이미지가_있다면_base64_다(built: Path, name: str) -> None:
    """지금은 캡처가 없다. 넣더라도 외부 파일을 가리키면 안 된다."""
    text = (built / name).read_text(encoding="utf-8")
    for source in re.findall(r'<img[^>]+src="([^"]*)"', text):
        assert source.startswith("data:"), f"외부 이미지: {source}"


@pytest.mark.parametrize("name", TARGETS)
def test_스타일과_스크립트를_품고_있다(built: Path, name: str) -> None:
    text = (built / name).read_text(encoding="utf-8")
    assert "<style>" in text
    assert "<script>" in text
    assert "@media print" in text, "인쇄 스타일이 없습니다."
    assert "Malgun Gothic" in text, "웹폰트 실패 시 폴백이 없습니다."


def test_캡처_자리가_이미지_태그를_만들지_않는다() -> None:
    """자리와 캡션만 표시한다. 사람이 나중에 넣는다."""
    body, _headings = render_markdown("![캡처 C-01] 첫 화면")
    assert "figure-slot" in body
    assert "<img" not in body
    assert "캡처 C-01" in body
    assert "첫 화면" in body


# ===================================================================== ③ 목차


@pytest.mark.parametrize("name", TARGETS)
def test_목차_링크가_모두_실제_절로_간다(built: Path, name: str) -> None:
    """**죽은 링크가 없어야 한다.**"""
    text = (built / name).read_text(encoding="utf-8")
    ids = set(re.findall(r'<h[1-6] id="([^"]+)"', text))
    links = re.findall(r"#toc[^>]*>.*?</nav>", text, flags=re.S)
    assert links, "목차가 없습니다."
    targets = re.findall(r'data-anchor="([^"]+)"', links[0])
    assert targets, "목차 항목이 없습니다."
    assert set(targets) <= ids, f"목차가 없는 절을 가리킵니다: {sorted(set(targets) - ids)}"


@pytest.mark.parametrize("name", TARGETS)
def test_목차가_모든_절을_담는다(built: Path, name: str) -> None:
    """h4 까지 담는다 — 링크가 걸린 소절을 목차에서 못 찾으면 곤란하다."""
    source = (built / name.replace(".html", ".md")).read_text(encoding="utf-8")
    _body, headings = render_markdown(source)
    expected = [item for item in headings if item.level in TOC_LEVELS]
    text = (built / name).read_text(encoding="utf-8")
    nav = re.findall(r"<nav id=\"toc\">.*?</nav>", text, flags=re.S)[0]
    targets = re.findall(r'data-anchor="([^"]+)"', nav)
    assert len(targets) == len(expected)


@pytest.mark.parametrize("name", TARGETS)
def test_문서_안_링크가_존재하는_앵커를_가리킨다(built: Path, name: str) -> None:
    """본문에서 `(#anchor)` 로 건 링크도 살아 있어야 한다."""
    text = (built / name).read_text(encoding="utf-8")
    ids = set(re.findall(r'<h[1-6] id="([^"]+)"', text))
    internal = {
        link for link in re.findall(r'<a href="#([^"]+)"', text) if not link.startswith("toc")
    }
    assert internal <= ids, f"없는 앵커를 가리킵니다: {sorted(internal - ids)}"


def test_id_가_겹치면_번호를_붙인다() -> None:
    _body, headings = render_markdown("## 같은 제목\n\n## 같은 제목\n")
    assert headings[0].anchor != headings[1].anchor


def test_명시한_id_를_그대로_쓴다() -> None:
    _body, headings = render_markdown("## 아무 제목 {#my-anchor}\n")
    assert headings[0].anchor == "my-anchor"
    assert headings[0].text == "아무 제목"


def test_슬러그가_한글을_남긴다() -> None:
    assert slugify("요금적용전력 3규칙") == "요금적용전력-3규칙"
    assert slugify("!!!") == "section"


# ===================================================================== ④ 앵커 30개


def test_앵커가_매뉴얼에_모두_있다() -> None:
    """없으면 화면 툴팁의 요지에 대응하는 전문이 없다 (16세션 4절)."""
    present = set(collect_anchors((DOCS / "MANUAL.md").read_text(encoding="utf-8")))
    missing = [key for key in anchor_keys() if key not in present]
    assert missing == [], f"매뉴얼에 없는 앵커: {missing}"


def test_생성된_매뉴얼_html_에도_앵커가_있다(built: Path) -> None:
    text = (built / MANUAL_FILENAME).read_text(encoding="utf-8")
    ids = set(re.findall(r'<h[1-6] id="([^"]+)"', text))
    assert set(anchor_keys()) <= ids


def test_앵커_수가_31개다() -> None:
    """**화면에서 없앤 자리는 앵커도 없다** (28세션 4·5절 · 36세션 1절).

    확실성·감도가 빠졌고, Word 는 애초에 앵커가 없었다. 36세션에 PPT 가 하나
    늘었다 (``ppt-report``).
    """
    assert len(ANCHORS) == 31
    assert "certainty" not in anchor_keys()
    assert "sensitivity" not in anchor_keys()


#: **앵커가 화면 ⓘ 에서 약속한 것마다 매뉴얼에 선 글자 하나** (S275 결정 2).
#: 앵커의 「매뉴얼이 담을 것」 을 쉼표 단위로 편 차례다 — 약속 100항목. 글자는 매뉴얼의 그
#: 대목에서 그대로 뜬 것이고(줄바꿈은 한 칸으로 편다) 다른 절에 선 대목도 그 절의 글자다.
#: **매뉴얼에서 그 대목을 고치면 여기 글자를 함께 고친다 · 지우면 약속(앵커)을 함께 걷는다.**
ANCHOR_PROMISES: dict[str, tuple[str, ...]] = {
    "column-detection": (
        "**판정은 세 단으로 한다.**",
        "utf-8-sig → cp949 → euc-kr → utf-8",
        "**빗나가는 양식의 예.**",
    ),
    "data-quality": (
        "**결측을 채우지 않는 이유.**",
        "**①은 근거로 세지 않고 나머지 둘이 다 있어야 정전이다**",
        "**편중 배수는 평일 피크 시간대의 결측률을 전체 결측률로 나눈 값이다.**",
        "kW 산정에서 빼고 kWh 합계에는 넣는다",
    ),
    "label-convention": (
        "하루가 `00:15` 로 시작해 `24:00` 으로 끝나",
        "**라벨에서 한 구간을 뺀 시각**",
        "**태양광 발전량도 같은 규약으로 붙인다.**",
    ),
    "contract-info": (
        "**셋은 성격이 다르다.**",
        "갑과 을을 가르는 자리다(기본공급약관 제57조 제2항",
        "**연간 이용시간** = 연간 사용량 ÷ 최대수요",
    ),
    "improvement-summary": (
        "1단계는 **진단만** 한다.",
        "| 선택요금 전환 절감액 | 2단계 **7.1** 카드 |",
        "**태양광 기여 가능성은 요금적용전력 대상 구간만 놓고 매긴다.**",
    ),
    "load-pattern": (
        "| 기저부하 비율 | 야간 평균 ÷ 주간 평균 |",
        "**운영 시간대는 어디에 쓰나.**",
    ),
    "peak-profile": (
        "**한 해 최대 하나가 아니라 달마다 낸다**",
        "**분포는 두 벌이고 섞지 않는다.**",
        "**피크 발생 시각은 라벨 그대로** 보고한다",
    ),
    "billing-demand": (
        "| ③ 계약전력 하한 |",
        "**①과 ②는 태양광에 반대 방향으로 작용한다.**",
        "**어느 쪽이 이기는지는 부하 형태가 정한다**",
    ),
    "charge-structure": (
        "**합계는 기본요금 + 전력량요금이다.**",
        "**시간대·계절 구분은 요금표가 정한 것을 그대로 읽는다.**",
        "**앞뒤 부분 월은 합쳐 한 달로 본다.**",
        "**기간이 12개월 미만이면 「연간」 이라 부르지 않는다.**",
    ),
    "contract-adequacy": (
        "**판정은 하한 하나로 갈린다**",
        "**가장 작은 달 ÷ 하한비율**",
        "하한 비율을 모르는 종별이라 **산출하지 않았다**",
    ),
    "measure-tariff-switch": (
        "기본요금 단가와 전력량요금 단가를 맞바꾼 것이라",
        "같은 계약종별·전압구분 안에서만 비교한다.",
        "절감액은 **요금을 처음부터 다시 계산한 값**",
    ),
    "measure-contract": (
        "최대수요전력계가 서서 요금적용전력 기준입니다",
        "**계약전력의 15%**",
        "{#excess-charge}",
    ),
    "measure-dr": (
        "전력시장운영규칙 제12장",
        "**신뢰성DR 과 다르다.**",
        "평일만 (토·일·공휴일 제외)",
        "실제 정산 기준량(CBL)은 전력거래소가 별도 방식으로 산정한다.",
        "**정산 단가는 우리가 만들 수 없다.**",
    ),
    "measure-power-factor": (
        "제43조 ② 1호 나",
        "| 감액이 멈추는 곳 | **97%** |",
        "**판정하는 시간대는 주간 08–22시다.**",
        "**야간(22–08시)은 진상만 본다.**",
        "무효전력 실측이 없다",
    ),
    "measure-solar": (
        "**20단계로 훑는 까닭.**",
        "| 12개월 환산 잉여 | 지금 용량에서 역송되는 양 (MWh).",
        "**발전량 예측은 피크를 과소 산출하는 경향이 있다.**",
        "**일몰 뒤 발전량은 태양 고도로 잘라 낸다.**",
    ),
    "pv-density": (
        "**밀도 하나가 이격 거리(GCR)와 경사각을 함께 정한다.**",
        "**환산은 `설치 가능 면적 × GCR ÷ 5` 다.**",
    ),
    "pv-cost": (
        "단가는 **DC 정격 기준**이며",
        "**인용할 만한 공개 자료를 확보하지 못했다.**",
        "**규모의 경제가 반영되지 않는다**",
    ),
    "measure-ess": (
        "**사양은 목표에서 거꾸로 낸다.**",
        "**하루치 초과 에너지 합의 연중 최댓값**",
        "규칙기반 단일 운전 전략이다.",
        "**충방전 차익거래는 이 값에 들어 있지 않다.**",
    ),
    "ess-cost-reference": (
        "**참고단가(LCOS 연구)는 투자비에 쓰지 않는다.**",
        "**하한선**이다 — 계통용 대형 ESS 기준이고",
        "**기본값으로 자동 적용하지 않는다**",
    ),
    "measure-surplus": (
        "| 상계거래(한전) | 우리가 계산한다 — 아래 참조 | 계약 변경 · 역송 계량기 |",
        "이 도구는 초기 검토용이라 인버터를 입력받지 않는다.",
        "**같은 계시로 다음 달에 이월**한다",
        "**자격요건을 판정하지 않는다.**",
        "단가를 지어내지 않는다",
    ),
    "weather-source": (
        "**출처와 라이선스.**",
        "기상 격자가 25~31 km 라",
        "**조회 차례는 캐시 → Open-Meteo → 사전 취득분이다.**",
        "인접 지역 기상으로 대체하지 않는다.",
    ),
    "combination": (
        "**합산효과의 절감액도 단순 합이 아니다.**",
        "**조합 표는 수단을 투자비 순으로 하나씩 쌓아 올린 값이다**",
    ),
    "payback": (
        "**단순 회수기간이다.**",
        "할인율·OPEX·열화·교체비를 넣지 않았다.",
        "| `미산출 — 투자비 미입력` | **단가를 넣지 않았다.** 0년이 아니다 |",
    ),
    "excel-report": (
        "시트짜리 통합문서다.",
        "| 감도 / 감도 상세 | 범위 / 시나리오 3행 원자료 |",
        "파일명에 날짜·시각이 붙는다",
    ),
    "ppt-report": (
        "**한 장에 한 가지만 담는다.**",
        "검토한 수단별 1장씩 **켠 수단만.**",
        "**부록에는 산출 근거만 싣는다.**",
    ),
    "rules-admin": (
        "| 🟥 **법령** | 약관·규칙 조문에서 온 값 |",
        "**코드에는 기본값이 없다.**",
        "요금 단가는 항목이 많아 엑셀로 왕복한다.",
    ),
    "rules-restore": (
        "| **직전 상태로** |",
        "**값은 세 층에 있다.**",
        "**출고 복원은 실행 전에 달라지는 항목을 모두 보여 주고 확인을 받습니다.**",
        "**파일이 손상되면 스스로 되살리고 반드시 알린다.**",
    ),
    "rules-expiry": (
        "| 요금 단가 | 12개월 (한전이 연 1~2회 개정한다) |",
        "**자동으로 받아 오지 않는다.**",
        "원문 확인처 링크가 함께 온다.",
    ),
    "weather-archive": (
        "필요한 격자만 받아 두는 구조이므로",
        "**부분 취득은 정상 상태다.**",
        "tools\\fetch_weather.py --start",
    ),
    "not-included": (
        "**이 도구는 기본요금과 전력량요금만 계산합니다.**",
        "절감액은 본 결과보다 다소 크게 나타납니다.**",
    ),
    "known-limits": (
        "| 태양광 용량 곡선은 모든 점에 같은 kWp당 단가를 쓴다 |",
        "| 인증·신고용 산출물이 아니다 |",
    ),
}


def test_앵커가_약속한_전문이_매뉴얼에_있다() -> None:
    """**화면 ⓘ 가 「매뉴얼에 있다」 고 한 것이 매뉴얼에 있다** (S275 결정 2).

    앵커 절이 있는지는 위 못이 본다. 이 못은 그 절이 **약속한 것을 담는지**를 본다 —
    75세션에 약속과 매뉴얼을 맞대니 마흔이 비어 있었고 S275 가 저장소의 근거(기술서 ·
    요구사항서 · 코드 머리글)로 다 채웠다. 약속마다 글자 하나를 문다.

    **한계 표는 줄 수까지 문다** — 산출물이 싣는 한계 목록(``KNOWN_LIMITS``)에 줄이 늘면
    매뉴얼 표에도 그 줄과 배경을 적는다.
    """
    from kwise.report.notices import KNOWN_LIMITS

    source = (DOCS / "MANUAL.md").read_text(encoding="utf-8")
    manual = " ".join(source.split())
    assert set(ANCHOR_PROMISES) == set(anchor_keys()), "앵커를 더하거나 뺐으면 약속 표도 고친다"
    missing = {
        key: gone
        for key, marks in ANCHOR_PROMISES.items()
        if (gone := [mark for mark in marks if " ".join(mark.split()) not in manual])
    }
    assert not missing, f"앵커가 약속한 대목이 매뉴얼에 없습니다: {missing}"

    limits = source.split("{#known-limits}", 1)[1].split("\n---", 1)[0]
    rows = [line for line in limits.splitlines() if line.startswith("| ")]
    assert len(rows) - 1 == len(KNOWN_LIMITS), (len(rows) - 1, len(KNOWN_LIMITS))


# ===================================================================== ⑤ 화면 툴팁


def test_툴팁이_제목과_요지를_함께_준다() -> None:
    """**링크가 아니라 툴팁이다** (16세션 4절). 화면에서 나가지 않고 요지를 읽는다."""
    tip = manual_tip("payback")
    assert tip.startswith("회수기간")
    assert "OPEX" in tip
    assert "](" not in tip and "http" not in tip


def test_모든_앵커가_툴팁을_낸다() -> None:
    """**제목 + 요지**다. 개선안 번호만 화면 순번으로 바뀐다 (27세션 2절)."""
    from kwise.ui.labels import measure_title

    for item in ANCHORS:
        tip = manual_tip(item.key)
        title = measure_title(item.title)
        assert title in tip
        assert len(tip) > len(title)


def test_등록되지_않은_앵커는_바로_실패한다() -> None:
    with pytest.raises(KeyError):
        manual_tip("없는-앵커")


# ===================================================================== 문법


def test_표를_표로_바꾼다() -> None:
    body, _headings = render_markdown("| 가 | 나 |\n|---|---|\n| 1 | 2 |\n")
    assert "<table>" in body
    assert "<th>가</th>" in body
    assert "<td>1</td>" in body


def test_코드블록에_복사_자리를_만든다() -> None:
    body, _headings = render_markdown("```\npytest\n```\n")
    assert 'class="code"' in body
    assert "<pre" in body
    assert "pytest" in body


def test_체크박스를_만든다() -> None:
    body, _headings = render_markdown("- [ ] 준비물\n- [x] 끝난 것\n")
    assert body.count('type="checkbox"') == 2
    assert "checked" in body


def test_코드_안의_별표는_굵게가_아니다() -> None:
    body, _headings = render_markdown("`a**b**c` 와 **진짜**\n")
    assert "a**b**c" in body
    assert "<strong>진짜</strong>" in body


def test_html_을_이스케이프한다() -> None:
    body, _headings = render_markdown("<script>alert(1)</script>\n")
    assert "<script>alert" not in body
    assert "&lt;script&gt;" in body


def test_인용과_가로줄() -> None:
    body, _headings = render_markdown("> 경고다\n\n---\n")
    assert "<blockquote>경고다</blockquote>" in body
    assert "<hr>" in body


def test_제목이_없어도_돈다() -> None:
    page, headings = render_html("그냥 문단 하나.", title="제목")
    assert headings == ()
    assert "그냥 문단 하나." in page
    assert '<nav id="toc">' in page


# ===================================================================== 중복 방지


def test_계산식은_기술서에만_있다() -> None:
    """매뉴얼은 근거를 가리키기만 한다 (중복 방지 규약)."""
    manual = (DOCS / "MANUAL.md").read_text(encoding="utf-8")
    assert "기술서" in manual, "매뉴얼이 기술서를 가리키지 않습니다."
    # 매뉴얼에 조문 색인 표를 옮겨 적지 않았는지 — 조문은 기술서 부록 C 한 곳이다
    assert manual.count("제68조") <= 1
    assert "부록 C" in (DOCS / "TECHNICAL.md").read_text(encoding="utf-8")


def test_사용법은_매뉴얼에만_있다() -> None:
    technical = (DOCS / "TECHNICAL.md").read_text(encoding="utf-8")
    assert "매뉴얼 2장" in technical, "기술서가 매뉴얼을 가리키지 않습니다."


def test_캡처_목록이_매뉴얼_자리와_맞는다() -> None:
    manual = (DOCS / "MANUAL.md").read_text(encoding="utf-8")
    captures = set(re.findall(r"!\[캡처 (C-\d+)\]", manual))
    listed = set(re.findall(r"\*\*(C-\d+)\*\*", (DOCS / "CAPTURES.md").read_text(encoding="utf-8")))
    assert captures == listed, f"매뉴얼 {sorted(captures)} vs 목록 {sorted(listed)}"
    assert len(captures) >= 8


def _manual_items() -> list[str]:
    """매뉴얼을 문단 · 목록 항목 하나씩으로 가르고 줄바꿈을 한 칸으로 편다."""
    manual = (DOCS / "MANUAL.md").read_text(encoding="utf-8")
    items: list[str] = []
    for block in re.split(r"\n\s*\n", manual):
        for item in re.split(r"\n(?=- )", block):
            items.append(" ".join(line.strip() for line in item.splitlines()))
    return items


#: 기본요금이 무엇에 붙는지 말하는 글자 — 걸리는 항목은 **그 항목 안에서** 갈래를 적는다.
BASE_FEE_CLAIMS = {
    "424": "관측 최대수요가 아니라 **요금적용전력**으로",
    "476": "**기본요금 비중이 높다** →",
    "527": "**밑단(기본요금)이 같은 높이로 이어지는 것이 정상이다.**",
    "750": "12개월간 기본요금을 올린다",
}


@pytest.mark.parametrize("anchor", BASE_FEE_CLAIMS.values(), ids=BASE_FEE_CLAIMS.keys())
def test_매뉴얼이_기본요금을_요금적용전력으로_단정하지_않는다(anchor: str) -> None:
    """**계약형(제68조 ②)에서 거짓인 단정을 갈래 없이 적지 않는다** (S170 1-3 · S182 3절).

    S182 에 xfail 을 걷고 보통 시험으로 갈았다. 앞 판은 옛 글자 넷을 전문에서 부분
    문자열로 물어 **넷을 다 고쳐야** 반응하고 조건을 앞에 붙인 고침을 못 알아봤다
    (S181 1-2). 이제 **항목마다 따로** 문다 — 글자가 선 문단 · 목록 항목 안에 같은
    매뉴얼 「기본요금이 계약전력에 붙는 종별」(초과사용부가금 절)과 같은 꼴의 갈래
    이름이 있어야 한다. 이름 앞의 번호는 S180 판 줄 번호다.
    """
    hits = [item for item in _manual_items() if anchor in item]
    assert hits, f"글자가 매뉴얼에서 사라졌다 — 못을 옮겨라: {anchor}"
    bare = [item for item in hits if "계약전력에 붙는 종별" not in item]
    assert bare == [], bare


def test_매뉴얼이_인용한_하향_경고가_산출물_글자와_같다() -> None:
    """**매뉴얼의 인용은 산출물 경고 글자 그대로다** (S181 1-3 · S182 3절).

    S180 이 요구사항서 9.4 원문과 사본 셋에서 「한 번의 초과가 12개월간 적용됩니다」
    를 뺐는데 매뉴얼 인용은 옛 글자로 남았다 — 매뉴얼 못(위)이 그 줄을 안 물었다.
    인용 바로 뒤 「이 경고는 **낮출 자리가 있을 때만 나온다**」 문단으로 자리를 찾는다.
    """
    from kwise.report.notices import CONTRACT_CHANGE_WARNING

    items = _manual_items()
    after = next(i for i, item in enumerate(items) if "이 경고는 **낮출 자리가 있을 때만" in item)
    quote = items[after - 1]
    assert quote.startswith("> "), quote
    assert quote.replace(" > ", " ").removeprefix("> ") == CONTRACT_CHANGE_WARNING
