r"""화면 실물 캡처 — **앱을 띄워 한 자리를 png 로 찍는다** (65세션).

    .venv\Scripts\python.exe tools\capture_screen.py --spot 요금구조
    .venv\Scripts\python.exe tools\capture_screen.py --list

64세션 5절에 화면을 확인할 일이 생겼는데 **모는 절차를 그 자리에서 손으로 짜고
버렸다.** `collaboration.md` 3절은 실물을 여는 절차를 코드로 세워 두었다고 적으며
`render_deck.py`(PPT) · `run_casestudy.py`(판정) · `screen_audit.py`(문구) 셋을
든다 — **거기에 화면만 없었다.** 앞으로 매 세션이 화면 확인을 요구하므로 세운다.

`screen_audit.py` 와 하는 일이 다르다. 그쪽은 ``streamlit.testing`` 으로 **문구를
모으는 것**이라 글자만 보고, 이 도구는 **진짜 브라우저에 그려진 그림**을 본다 —
잘림·겹침·자리는 그려 봐야 안다.

**확인용이다. 매뉴얼에 넣을 캡처가 아니다** (`docs\CAPTURES.md` 와 엮지 않는다).
그래서 산출물은 ``output\`` 이 아니라 **``PROJECT_CACHE``** 아래에 둔다.

**스크롤은 요소 기준으로 한다.** 64세션에 ``window.scrollTo`` 가 안 먹었다 —
Streamlit 은 창이 아니라 **내부 컨테이너**가 스크롤한다. 찍을 자리를
``scrollIntoView`` 로 올려야 뷰포트에 들어온다.

playwright 는 **앱이 쓰는 것이 아니라 확인용**이라 ``[project.dependencies]`` 가
아니라 ``[project.optional-dependencies].dev`` 에 있다. 없으면 아래를 돌린다.

    .venv\Scripts\python.exe -m pip install -e ".[dev]"
    .venv\Scripts\python.exe -m playwright install chromium
"""

from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

# **`sys.path` 를 세운 뒤에 들여온다.** 위에 두면 저장소를 설치하지 않은
# 환경에서 이 도구가 통째로 못 뜬다 (`render_deck.py` 와 같은 규약).
from kwise.pv import cache_root  # noqa: E402

APP = PROJECT_ROOT / "streamlit_app.py"
DEFAULT_USAGE = PROJECT_ROOT / "input" / "전기사용량_소형건물.xlsx"

#: 앱이 뜨기를 기다리는 상한. 넘으면 죽이고 그 사실을 보고한다.
BOOT_TIMEOUT_S = 90.0
#: 계산이 끝나기를 기다리는 상한. 대형 자료는 태양광·ESS 까지 돌면 오래 걸린다.
RENDER_TIMEOUT_MS = 300_000


@dataclass(frozen=True)
class Contract:
    """계약 정보 네 칸. **확정해야 금액이 나온다** — 안 하면 요금 구조 장이 없다."""

    contract_type: str
    voltage: str
    contract_kw: float
    option: str
    #: 주간 지상역률 (%). ``None`` 이면 「역률 (선택)」 을 안 건드린다 —
    #: 약관 제42조 간주값(지상 92%)이 그대로 선다. 값을 주면 그 접힌 칸을 펴서
    #: 넣고 「역률 반영」 까지 누른다 (S157 1절 · 미해결 ②-43).
    lagging_pct: float | None = None


@dataclass(frozen=True)
class Spot:
    """찍을 자리 하나. **이름이 곧 재현 조건이다** (`render_deck.py` 와 같은 규약)."""

    key: str
    title: str
    #: 이 글자가 있는 요소를 찾아 그 위로 스크롤한 뒤 찍는다.
    anchor: str
    #: 앵커가 뜨기 전에 먼저 기다릴 글자. 없으면 앵커를 그대로 기다린다.
    wait_for: str = ""
    contract: Contract | None = None
    #: 누를 탭 (`kwise.ui.nav.TABS`). 비면 1단계에 머문다.
    #:
    #: **세 단계는 탭이라 한 실행에 다 그려지는데**(`nav.py`) 뷰포트에는 고른
    #: 탭 하나만 보인다. 도구가 탭을 안 눌러 **2·3단계 자리가 하나도 없었다** —
    #: 미해결 ②-8 의 뿌리가 이것이다 (S157 1절).
    tab: str = ""
    #: 켤 개선안 카드 이름 조각. **2·3단계는 카드를 켜야 내용이 선다** —
    #: 카드는 접힌 채로 시작한다 (`views\measures.py`).
    measures: tuple[str, ...] = ()
    #: 올릴 사용량 파일. 비면 ``--usage`` 를 쓴다. **벌마다 자료가 다르다** —
    #: 산업용(갑)Ⅰ 저압은 제 실측이 있어야 역률 100 이 선다.
    usage: Path | None = None


#: 용인 소규모 건물 · 갑Ⅱ 고압A 선택Ⅱ (61세션에 확보한 실측).
#: `render_deck.py` 의 `small-a2` 벌과 **같은 조건**이라 덱과 화면을 맞대 볼 수 있다.
YONGIN_A2 = Contract(
    contract_type="일반용전력(갑)Ⅱ",
    voltage="고압A",
    contract_kw=290.0,
    option="선택Ⅱ",
)

#: 교육용(갑) 고압A 선택Ⅰ. `render_deck.py` 의 `small-edu-a` 벌과 **같은 조건**이다.
#: **89세션이 고친 자리를 화면으로 보는 자리** — 고치기 전에는 기본요금이
#: 계약전력 300 kW 로 매겨지고 「계약전력 … kW 기준입니다」 가 붙었다.
EDUCATION_A_HIGH = Contract(
    contract_type="교육용전력(갑)",
    voltage="고압A",
    contract_kw=300.0,
    option="선택Ⅰ",
)

#: 화면 감사(`tools\screen_audit.py` 의 `CASES`)가 세는 **넷**과 같은 조건이다.
#: **같은 조건으로 찍어야 문구 수와 그림을 맞대 볼 수 있다** (S157 2-1).
GENERAL_B = Contract("일반용전력(을)", "고압A", 6_000.0, "선택Ⅱ")
GENERAL_A1_LOW = Contract("일반용전력(갑)Ⅰ", "저압", 200.0, "전체시간")
EDUCATION_A_LOW = Contract("교육용전력(갑)", "저압", 200.0, "전체시간")

#: **다섯째 — `render_deck.py` 의 `small-ind-a1` 벌과 같은 조건이다.**
#: S155·S156 이 고친 자리가 이 벌에서만 그려진다 — 실물 청구서가 따라온 실측이라
#: **역률이 100** 이고, 그래서 상한(97%) 위 갈래와 「삼각형이 선이 되는」 자리가
#: 여기서만 선다. 자료도 이 벌 것을 쓴다 (`usage`).
SMALL_IND_A1 = Contract("산업용전력(갑)Ⅰ", "저압", 75.0, "전체시간", lagging_pct=100.0)
IND_LOW_XLSX = PROJECT_ROOT / "input" / "전력사용량_산업용(갑)저압.xlsx"

#: 역률 상한(97%) 갈래를 화면으로 보는 조건. **자료가 아니라 역률이 가른다** —
#: 같은 을 조건에서 주간 지상역률만 갈아 세 자리를 뽑는다 (S157 2-2).
GENERAL_B_PF98 = replace(GENERAL_B, lagging_pct=98.0)
GENERAL_B_PF97 = replace(GENERAL_B, lagging_pct=97.0)

#: 2단계에서 켤 카드. **이름은 화면 표기**(`kwise.ui.labels.measure_title`)다 —
#: 코드의 7.x 가 아니라 1~6 으로 뜬다.
ALL_MEASURES: tuple[str, ...] = (
    "1. 선택요금 전환",
    "2. 계약전력 조정",
    "3. 경제성DR",
    "4. 역률 개선",
    "5. 태양광",
    "6. ESS",
)

SPOTS: tuple[Spot, ...] = (
    Spot(
        key="요금구조",
        title="1단계 · 진단 › 현재 요금 구조",
        anchor="현재 요금 구조",
        wait_for="피크 특성",
        contract=YONGIN_A2,
    ),
    Spot(
        key="요금구조-교육갑",
        title="1단계 · 진단 › 현재 요금 구조 (교육용(갑) 고압A)",
        anchor="현재 요금 구조",
        wait_for="피크 특성",
        contract=EDUCATION_A_HIGH,
    ),
    Spot(
        key="피크특성",
        title="1단계 · 진단 › 피크 특성",
        anchor="피크 특성",
        wait_for="피크 특성",
    ),
    Spot(
        key="데이터품질",
        title="1단계 · 진단 › 데이터 품질",
        anchor="데이터 품질",
        wait_for="데이터 품질",
    ),
    Spot(
        key="첫화면",
        title="앱 첫 화면 (파일을 올리기 전)",
        anchor="kWise",
        wait_for="",
    ),
    # ------------------------------------------------- 1단계 · 감사 조건 넷과 다섯째
    Spot(
        key="요금구조-을",
        title="1단계 · 진단 › 현재 요금 구조 (일반용(을) 고압A 선택Ⅱ · 6,000 kW)",
        anchor="현재 요금 구조",
        wait_for="피크 특성",
        contract=GENERAL_B,
    ),
    Spot(
        key="요금구조-갑1",
        title="1단계 · 진단 › 현재 요금 구조 (일반용(갑)Ⅰ 저압 · 200 kW)",
        anchor="현재 요금 구조",
        wait_for="피크 특성",
        contract=GENERAL_A1_LOW,
    ),
    Spot(
        key="요금구조-교육갑저압",
        title="1단계 · 진단 › 현재 요금 구조 (교육용(갑) 저압 · 200 kW)",
        anchor="현재 요금 구조",
        wait_for="피크 특성",
        contract=EDUCATION_A_LOW,
    ),
    Spot(
        key="요금구조-산업갑1저압",
        title="1단계 · 진단 › 현재 요금 구조 (산업용(갑)Ⅰ 저압 · 75 kW · 역률 100)",
        anchor="현재 요금 구조",
        wait_for="피크 특성",
        contract=SMALL_IND_A1,
        usage=IND_LOW_XLSX,
    ),
    Spot(
        key="데이터품질-산업갑1저압",
        title="1단계 · 진단 › 데이터 품질 (5개월 실측)",
        anchor="데이터 품질",
        wait_for="데이터 품질",
        usage=IND_LOW_XLSX,
    ),
    Spot(
        key="기간경고-산업갑1저압",
        title="1단계 · 진단 › 지표 넷과 안내 (12개월 미만 경고가 서는 자리)",
        # **경고는 「데이터 품질」 이 아니라 그 위 안내 블록에 있다**
        # (`views\diagnose.py::_notice_block` · 「경고는 위쪽이 이미 냈다」).
        # S157 2절에 데이터 품질로 찍었더니 경고가 화면 밖으로 밀렸다.
        anchor="분석 기간",
        wait_for="데이터 품질",
        usage=IND_LOW_XLSX,
    ),
    # ------------------------------------------------- 2단계 · 역률 상한 갈래 셋
    Spot(
        key="역률-98",
        title="2단계 › 역률 개선 (현재 98% · 상한 위 — 「개선 여지 없음」)",
        anchor="전력삼각형",
        wait_for="피크 특성",
        contract=GENERAL_B_PF98,
        tab="2단계 · 개선 수단",
        measures=("4. 역률 개선",),
    ),
    Spot(
        key="역률-97",
        title="2단계 › 역률 개선 (현재 97% · 정확히 상한 — 두 삼각형이 겹친다)",
        anchor="전력삼각형",
        wait_for="피크 특성",
        contract=GENERAL_B_PF97,
        tab="2단계 · 개선 수단",
        measures=("4. 역률 개선",),
    ),
    Spot(
        key="역률-100",
        title="2단계 › 역률 개선 (산업용(갑)Ⅰ 저압 · 역률 100 — 삼각형이 선이 된다)",
        anchor="전력삼각형",
        wait_for="피크 특성",
        contract=SMALL_IND_A1,
        usage=IND_LOW_XLSX,
        tab="2단계 · 개선 수단",
        measures=("4. 역률 개선",),
    ),
    # ------------------------------------------------- 2·3단계 전체
    Spot(
        key="수단",
        title="2단계 · 개선 수단 (카드 여섯을 다 켠 머리)",
        anchor="2단계 · 개선 수단",
        wait_for="피크 특성",
        contract=GENERAL_B,
        tab="2단계 · 개선 수단",
        measures=ALL_MEASURES,
    ),
    Spot(
        key="조합",
        title="3단계 · 개선안 조합 (합산효과까지)",
        anchor="합산효과",
        wait_for="피크 특성",
        contract=GENERAL_B,
        tab="3단계 · 개선안 조합",
        measures=ALL_MEASURES,
    ),
)

BY_KEY = {spot.key: spot for spot in SPOTS}


def _free_port() -> int:
    """빈 포트를 얻는다. **고정하지 않는다** — 앞 세션이 안 내린 앱과 부딪친다."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _port_is_open(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


#: 브라우저 바이너리가 없을 때 낼 말. 꾸러미가 있어도 이쪽이 따로 빈다.
_INSTALL_BROWSER = "    .venv\\Scripts\\python.exe -m playwright install chromium"


def _require_playwright() -> None:
    """**조용히 실패하지 않게 한다.** 무엇이 없고 무엇을 돌리면 되는지 적는다.

    **꾸러미가 있는지만 본다.** 브라우저 바이너리까지 떠보려고 드라이버를
    띄웠다 내리면 성공한 실행에서도 ``Task was destroyed`` 가 섞여 나온다 —
    바이너리는 :func:`capture` 가 실제로 띄울 때 걸리고, 그 자리에서 같은 말을
    낸다. **떠보기 위해 켜지 않는다.**
    """
    try:
        import playwright.sync_api  # noqa: F401
    except ModuleNotFoundError:
        sys.exit(
            "playwright 가 없습니다. 개발용 선택 의존성입니다 —\n"
            '    .venv\\Scripts\\python.exe -m pip install -e ".[dev]"\n'
            f"{_INSTALL_BROWSER}"
        )


def _start_app(port: int) -> subprocess.Popen[bytes]:
    """앱을 띄우고 포트가 열릴 때까지 기다린다. **상한을 넘으면 죽인다.**"""
    process = subprocess.Popen(
        [
            str(PROJECT_ROOT / ".venv" / "Scripts" / "streamlit.exe"),
            "run",
            str(APP),
            "--server.port",
            str(port),
            "--server.headless",
            "true",
        ],
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + BOOT_TIMEOUT_S
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"앱이 뜨자마자 끝났습니다 (종료 코드 {process.returncode}).")
        if _port_is_open(port):
            return process
        time.sleep(0.5)
    _stop_app(process, port)
    raise RuntimeError(f"앱이 {BOOT_TIMEOUT_S:.0f}초 안에 뜨지 않아 죽였습니다 (포트 {port}).")


def _stop_app(process: subprocess.Popen[bytes], port: int) -> bool:
    """앱을 내리고 **정말 내려갔는지 포트로 확인한다.**

    64세션 마감에 「돌고 있는 셸이 없는지」 를 손으로 확인했다. 그 확인을
    도구 안으로 들인다 — 도구가 남긴 것은 도구가 치운다.
    """
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=15)
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        if not _port_is_open(port):
            return True
        time.sleep(0.5)
    return False


def _wait_idle(page: object) -> None:
    """Streamlit 이 다시 그리기를 끝낼 때까지 기다린다. **고정 대기를 쓰지 않는다.**

    ``[data-testid="stApp"]`` 의 ``data-test-script-state`` 가 ``notRunning`` 이
    될 때까지 본다. 고정 대기는 기계가 놀 때도 먼저 끝날 수 있고, 그러면 다시
    그리는 도중에 누르게 되어 요소가 DOM 에서 떨어져 나간다 —
    S140 이 세 판 다 ``전압구분`` 목록에서 ``element was detached`` 로 죽었다.
    """
    page.wait_for_selector(  # type: ignore[attr-defined]
        '[data-testid="stApp"][data-test-script-state="notRunning"]',
        timeout=RENDER_TIMEOUT_MS,
    )


def _pick(page: object, label: str, want: str) -> None:
    """Streamlit 드롭다운 하나를 고른다. 라벨로 상자를 찾고 목록에서 값을 누른다."""
    _wait_idle(page)
    box = page.locator(f'[data-testid="stSelectbox"]:has-text("{label}")').first  # type: ignore[attr-defined]
    box.click()
    page.locator('[role="option"]', has_text=want).first.click()  # type: ignore[attr-defined]
    _wait_idle(page)


def _fill_contract(page: object, contract: Contract) -> None:
    """계약 정보 네 칸을 채우고 확정한다.

    **확정을 안 누르면 금액이 안 나온다** — 화면이 「계약 정보가 없어 요금 구조와
    절감액을 산출하지 않았습니다」 로 멈춘다 (64세션 5절에 여기서 한 번 헛돌았다).
    """
    _pick(page, "계약종별", contract.contract_type)
    _pick(page, "전압구분", contract.voltage)
    kw = page.locator('[data-testid="stNumberInput"]:has-text("계약전력")').first.locator("input")  # type: ignore[attr-defined]
    kw.fill(f"{contract.contract_kw:.0f}")
    kw.press("Enter")
    _wait_idle(page)
    _pick(page, "선택요금", contract.option)
    page.get_by_text("계약 정보 확정", exact=True).first.click()  # type: ignore[attr-defined]
    if contract.lagging_pct is not None:
        _set_lagging(page, contract.lagging_pct)


def _set_lagging(page: object, pct: float) -> None:
    """「역률 (선택)」 을 펴서 주간 지상역률을 넣고 **반영까지 누른다.**

    **접힌 칸이라 펴야 보인다** (`views\\diagnose.py::_power_factor_block`).
    반영 단추는 값이 갈렸을 때만 서므로 **계약 정보 확정 뒤에** 부른다 — 확정
    전에는 `form` 이 없어 단추 자체가 안 그려진다.

    이것이 미해결 ②-43 「화면 캡처 도구가 역률을 못 준다」 다. 역률은 화면이
    받는 값이 아니라 **계약 정보 옆 칸**이 받는 값이라 자리를 못 찾고 있었다.
    """
    _wait_idle(page)
    box = page.locator(  # type: ignore[attr-defined]
        '[data-testid="stNumberInput"]:has-text("주간 지상역률")'
    ).first.locator("input")
    # **펴졌는지 보고 누른다.** 확정 단추가 화면을 다시 그리는 사이에 눌러
    # 접힌 칸을 도로 접는 일이 났다 — S157 2절에 `역률-97` 이 그렇게 죽었다
    # (같은 명령의 `역률-98` 은 살았다). **누른 횟수가 아니라 결과를 본다.**
    for _ in range(3):
        if box.is_visible():
            break
        page.get_by_text("역률 (선택)", exact=True).first.click()  # type: ignore[attr-defined]
        _wait_idle(page)
    box.fill(f"{pct:.1f}")
    box.press("Enter")
    _wait_idle(page)
    page.get_by_text("역률 반영", exact=True).first.click()  # type: ignore[attr-defined]
    _wait_idle(page)


def _anchor(page: object, text: str) -> object:
    """앵커 글자를 **보이는 것 가운데** 찾는다.

    **숨은 탭의 글자를 물면 안 된다** (S157 1절). 세 단계가 한 실행에 다
    그려지므로 안 고른 탭의 글도 DOM 에 있다 — `조합` 자리가 앵커 「합산효과」
    를 기다리다 2단계의 「… 3단계 합산효과에서 별도로 산정합니다」 를 잡고
    **5분을 꼬박 기다리다 죽었다.** 그 글은 영영 안 보인다.

    ``exact`` 로 좁히는 것만으로는 모자란다 — 접힌 카드 안의 같은 글자도
    DOM 에 있다.
    """
    return page.get_by_text(text, exact=True).locator("visible=true").first  # type: ignore[attr-defined]


def _open_tab(page: object, name: str) -> None:
    """단계 탭 하나를 누른다. **셋이 다 그려져 있어도 보이는 것은 하나다.**"""
    _wait_idle(page)
    page.get_by_role("tab", name=name).first.click()  # type: ignore[attr-defined]
    _wait_idle(page)


def _turn_on(page: object, label: str) -> None:
    """개선안 카드 하나를 켠다. **카드는 접힌 채로 시작한다** (`views\\measures.py`).

    체크박스 라벨이 곧 카드 이름이라 그 상자를 눌러야 한다 — 켜기 전에는
    같은 글자가 화면에 그 하나뿐이고, 켠 뒤에 확장 패널 제목으로 하나 더 생긴다.
    """
    _wait_idle(page)
    page.locator(  # type: ignore[attr-defined]
        f'[data-testid="stCheckbox"]:has-text("{label}")'
    ).first.click()
    _wait_idle(page)


def capture(spot: Spot, usage: Path, out_dir: Path, *, port: int, width: int, height: int) -> Path:
    """한 자리를 찍어 png 경로를 돌려준다. **앱은 반드시 내린다.**"""
    from playwright.sync_api import sync_playwright

    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out_dir.mkdir(parents=True, exist_ok=True)
    # **실행마다 타임스탬프를 붙인다** (`CLAUDE.md`). 낡은 png 가 새것처럼 보이면
    # 확인 자체가 틀어진다.
    png = out_dir / f"{spot.key}_{stamp}.png"

    process = _start_app(port)
    try:
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch()
            except Exception as exc:  # pragma: no cover - 환경에 달렸다
                # **꾸러미는 있는데 바이너리가 없는 경우가 여기서 걸린다.**
                # playwright 가 내는 말은 길어 묻히므로 한 줄로 다시 적는다.
                raise SystemExit(f"크로미움을 못 띄웠습니다 ({exc}).\n{_INSTALL_BROWSER}") from exc
            try:
                page = browser.new_page(viewport={"width": width, "height": height})
                page.goto(f"http://127.0.0.1:{port}", wait_until="networkidle", timeout=60_000)
                page.wait_for_timeout(3_000)
                if spot.key != "첫화면":
                    page.set_input_files("input[type=file]", str(usage))
                    page.wait_for_selector(
                        f"text={spot.wait_for or spot.anchor}", timeout=RENDER_TIMEOUT_MS
                    )
                    page.wait_for_timeout(4_000)
                if spot.contract is not None:
                    _fill_contract(page, spot.contract)
                # **카드는 2단계 탭 안에 있다.** 켜는 일을 먼저 하고 탭을 옮긴다 —
                # 3단계 자리도 2단계에서 켠 것을 본다 (`views\compare.py`).
                if spot.measures:
                    _open_tab(page, "2단계 · 개선 수단")
                    for label in spot.measures:
                        _turn_on(page, label)
                if spot.tab:
                    _open_tab(page, spot.tab)
                    if spot.tab.startswith("3단계"):
                        # **누르지 않으면 합산효과가 통째로 빠진다** (33세션 5절).
                        page.get_by_text("합산효과 계산", exact=True).first.click()
                        _wait_idle(page)
                if spot.contract is not None or spot.tab:
                    _anchor(page, spot.anchor).wait_for(timeout=RENDER_TIMEOUT_MS)  # type: ignore[attr-defined]
                page.wait_for_timeout(6_000)
                # **요소 기준으로 스크롤한다.** `window.scrollTo` 는 안 먹는다 —
                # Streamlit 은 창이 아니라 내부 컨테이너가 스크롤한다 (64세션 5절).
                anchor = _anchor(page, spot.anchor)
                anchor.evaluate("el => el.scrollIntoView({block: 'start'})")  # type: ignore[attr-defined]
                page.wait_for_timeout(2_500)
                page.screenshot(path=str(png))
            finally:
                browser.close()
    finally:
        if not _stop_app(process, port):
            print(f"[경고] 포트 {port} 가 아직 열려 있습니다. 손으로 확인하십시오.")
        else:
            print(f"앱을 내렸습니다 (포트 {port} 닫힘 확인).")
    return png


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="화면 실물 캡처")
    parser.add_argument(
        "--spot", action="append", help="찍을 자리. 여러 번 줄 수 있다. 없으면 요금구조"
    )
    parser.add_argument("--usage", type=Path, default=DEFAULT_USAGE, help="올릴 사용량 파일")
    parser.add_argument(
        "--out", type=Path, default=None, help="산출 폴더 (기본 PROJECT_CACHE\\screens)"
    )
    parser.add_argument("--port", type=int, default=0, help="쓸 포트. 0 이면 빈 포트를 고른다")
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=1100)
    parser.add_argument("--list", action="store_true", help="찍을 수 있는 자리를 보인다")
    args = parser.parse_args(argv)

    if args.list:
        for spot in SPOTS:
            marks = []
            if spot.contract is not None:
                marks.append("계약 정보 확정")
            if spot.contract is not None and spot.contract.lagging_pct is not None:
                marks.append(f"역률 {spot.contract.lagging_pct:.0f}%")
            if spot.measures:
                marks.append(f"카드 {len(spot.measures)}")
            if spot.usage is not None:
                marks.append(spot.usage.name)
            mark = f"  ({' · '.join(marks)})" if marks else ""
            print(f"  {spot.key:22s} {spot.title}{mark}")
        return 0

    _require_playwright()

    keys = args.spot or ["요금구조"]
    unknown = [key for key in keys if key not in BY_KEY]
    if unknown:
        parser.error(f"모르는 자리: {', '.join(unknown)} — --list 로 확인하십시오")
    # **벌마다 자료가 다를 수 있다** — 자리가 제 것을 들면 그것이 이긴다.
    wanted = {BY_KEY[key].usage or args.usage for key in keys}
    missing = sorted(str(path) for path in wanted if not path.exists())
    if missing:
        parser.error(f"사용량 파일이 없습니다: {', '.join(missing)}")

    out_dir = args.out if args.out is not None else cache_root() / "screens"
    for key in keys:
        spot = BY_KEY[key]
        print(f"[{spot.key}] {spot.title}")
        png = capture(
            spot,
            spot.usage or args.usage,
            out_dir,
            port=args.port or _free_port(),
            width=args.width,
            height=args.height,
        )
        size_kb = png.stat().st_size / 1024
        print(f"  {png} ({size_kb:,.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
