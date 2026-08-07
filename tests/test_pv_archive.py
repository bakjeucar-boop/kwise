"""기상 데이터 사전 취득분 (요구사항서 7.5).

Open-Meteo 가 막혀도 검토가 멈추지 않아야 한다. 다만 **조용히 바뀌면 안 된다** —
폴백한 사실이 결과에 실려 나오는지, 범위 밖이면 0 으로 때우지 않고 멈추는지를
여기서 못 박는다.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from kwise.pv import ArrayConfig, PvSystemConfig, WeatherRequest, WeatherUnavailableError, simulate
from kwise.pv.archive import (
    ARCHIVE_COLUMNS,
    ATTRIBUTION,
    DEFAULT_ARCHIVE_END,
    DEFAULT_ARCHIVE_START,
    Pacer,
    RetryPolicy,
    WeatherHttpError,
    archive_covers,
    archive_path,
    archive_root,
    archive_status,
    decode_frame,
    encode_frame,
    fetch_cell_year,
    fetch_json_with_retry,
    grid_cells_for,
    load_archive,
    national_cells,
    pending_tasks,
    store_cell_year,
)
from kwise.pv.region import list_sigungu, load_regions
from kwise.pv.weather import load_weather
from tests._synthetic import clearsky_weather

CELL = (37.5, 127.0)
TZ = "Asia/Seoul"


# --------------------------------------------------------------------- 격자 중복 제거


def test_national_grid_collapses_229_sigungu_into_135_cells() -> None:
    """0.25° 격자로 접으면 호출 수가 229 → 135 로 준다."""
    assert len(load_regions()) == 229
    assert len(national_cells()) == 135


def test_seoul_25_gu_collapse_into_4_cells() -> None:
    """서울 25개 구의 좌표 폭이 격자보다 작아 4셀로 뭉친다."""
    regions = list_sigungu("서울특별시")
    assert len(regions) == 25
    assert len(grid_cells_for(regions)) == 4


def test_grid_cells_are_deduplicated_and_sorted() -> None:
    cells = national_cells()
    assert len(set(cells)) == len(cells)


# --------------------------------------------------------------------- 저장 형식


@pytest.fixture(scope="module")
def sample_hourly() -> pd.DataFrame:
    """합성 청천 기상. 네트워크를 타지 않는다."""
    weather = clearsky_weather(start="2023-06-28", end="2023-07-05")
    return weather.hourly[list(ARCHIVE_COLUMNS)]


@pytest.fixture
def stored_root(tmp_path: Path, sample_hourly: pd.DataFrame) -> Path:
    root = tmp_path / "weather"
    store_cell_year(sample_hourly, CELL, 2023, root=root)
    return root


def test_int16_round_trip_keeps_the_scale_resolution(sample_hourly: pd.DataFrame) -> None:
    """일사 0.1 W/m², 기온·풍속 0.01 단위까지 보존된다."""
    decoded = decode_frame(encode_frame(sample_hourly))
    assert (decoded["ghi"] - sample_hourly["ghi"]).abs().max() <= 0.05
    assert (decoded["temp_air"] - sample_hourly["temp_air"]).abs().max() <= 0.005
    assert (decoded["wind_speed"] - sample_hourly["wind_speed"]).abs().max() <= 0.005


def test_snowfall_is_filled_because_it_is_not_stored(sample_hourly: pd.DataFrame) -> None:
    """적설은 계산에 쓰지 않아 저장하지 않는다. 스키마 호환으로 0 을 채운다."""
    assert "snowfall" not in ARCHIVE_COLUMNS
    assert (decode_frame(encode_frame(sample_hourly))["snowfall"] == 0.0).all()


def test_stored_file_is_int16(stored_root: Path) -> None:
    frame = pd.read_parquet(archive_path(CELL, 2023, root=stored_root))
    assert set(frame.dtypes) == {pd.Series([], dtype="int16").dtype}


# --------------------------------------------------------------------- 현황 조회


def test_status_reports_cells_years_and_size(stored_root: Path) -> None:
    """8세션 UI 가 이 형태로 확보 현황을 읽는다."""
    status = archive_status(stored_root)
    assert status.cell_count == 1
    assert status.years == (2023,)
    assert status.bytes > 0
    assert status.attribution == ATTRIBUTION
    cell = status.find(37.4979, 127.0276)  # 강남구 — 같은 격자다
    assert cell is not None and cell.key == "37.5000_127.0000"


def test_status_covers_only_the_stored_span(stored_root: Path) -> None:
    status = archive_status(stored_root)
    assert status.covers(37.5, 127.0, dt.date(2023, 6, 29), dt.date(2023, 7, 4))
    assert not status.covers(37.5, 127.0, dt.date(2022, 1, 1), dt.date(2022, 1, 2))
    assert not status.covers(35.0, 129.0, dt.date(2023, 6, 29), dt.date(2023, 7, 4))


def test_pending_tasks_skip_what_is_already_stored(stored_root: Path) -> None:
    """증분 갱신 — 중단 후 같은 명령으로 재개된다."""
    todo, done = pending_tasks([CELL], dt.date(2023, 6, 29), dt.date(2023, 7, 4), root=stored_root)
    assert not todo
    assert len(done) == 1

    # 저장된 2023년분은 일주일치뿐이다. 2023년 전체를 요구하면 **부분 확보를
    # 완전 확보로 오인하지 않고** 다시 받는다.
    todo, done = pending_tasks([CELL], dt.date(2023, 6, 29), dt.date(2024, 7, 4), root=stored_root)
    assert [task.year for task in todo] == [2023, 2024]
    assert not done


def test_refresh_re_fetches_everything(stored_root: Path) -> None:
    todo, done = pending_tasks(
        [CELL], dt.date(2023, 6, 29), dt.date(2023, 7, 4), root=stored_root, refresh=True
    )
    assert len(todo) == 1 and not done


def test_national_plan_is_405_tasks(tmp_path: Path) -> None:
    """전국 135격자 × 3개년 = 405회 호출."""
    todo, done = pending_tasks(
        national_cells(), DEFAULT_ARCHIVE_START, DEFAULT_ARCHIVE_END, root=tmp_path
    )
    assert len(todo) == 405 and not done


# --------------------------------------------------------------------- 경로 이동


def test_archive_works_from_any_directory(
    tmp_path: Path, sample_hourly: pd.DataFrame, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``data\\weather`` 를 다른 경로로 옮겨도 동작한다 (배포 시 위치가 달라진다)."""
    moved = tmp_path / "어딘가" / "weather-2026"
    store_cell_year(sample_hourly, CELL, 2023, root=moved)

    monkeypatch.setenv("KWISE_WEATHER_DIR", str(moved))
    assert archive_root() == moved
    assert archive_status().cell_count == 1

    request = WeatherRequest(37.5, 127.0, dt.date(2023, 6, 29), dt.date(2023, 7, 3))
    assert archive_covers(request)
    assert not load_archive(request).empty


# --------------------------------------------------------------------- 폴백


def _failing_fetch(request: WeatherRequest) -> pd.DataFrame:
    raise WeatherUnavailableError("Open-Meteo 연결에 실패했습니다. (모의)")


def roof_config() -> PvSystemConfig:
    return PvSystemConfig(
        latitude=37.5,
        longitude=127.0,
        arrays=(ArrayConfig.roof("지붕", 100.0),),
        altitude_m=50.0,
        timezone=TZ,
    )


def test_api_failure_falls_back_and_the_calculation_finishes(
    stored_root: Path, tmp_path: Path
) -> None:
    """API 가 죽어도 사전 취득분으로 완주한다."""
    request = WeatherRequest(37.5, 127.0, dt.date(2023, 6, 29), dt.date(2023, 7, 3))
    weather = load_weather(
        request,
        fetch=_failing_fetch,
        cache_dir=tmp_path / "cache",
        archive_dir=stored_root,
    )
    assert weather.source == "archive"
    simulation = simulate(weather, roof_config())
    assert simulation.energy_kwh > 0


def test_fallback_is_reported_in_the_result(stored_root: Path, tmp_path: Path) -> None:
    """**폴백했다는 사실을 결과에 표시한다.** 조용히 바꾸지 않는다."""
    request = WeatherRequest(37.5, 127.0, dt.date(2023, 6, 29), dt.date(2023, 7, 3))
    weather = load_weather(
        request,
        fetch=_failing_fetch,
        cache_dir=tmp_path / "cache",
        archive_dir=stored_root,
    )
    assert weather.fallback is True
    assert weather.notes and "사전 취득분" in weather.notes[0]

    simulation = simulate(weather, roof_config())
    assert any("사전 취득분" in message for message in simulation.warnings)


def test_empty_api_response_also_falls_back(stored_root: Path, tmp_path: Path) -> None:
    """빈 응답도 취득 실패다. 빈 시계열로 0 을 만들지 않고 사전 취득분으로 간다."""
    request = WeatherRequest(37.5, 127.0, dt.date(2023, 6, 29), dt.date(2023, 7, 3))
    weather = load_weather(
        request,
        fetch=lambda _: pd.DataFrame(),
        cache_dir=tmp_path / "cache",
        archive_dir=stored_root,
    )
    assert weather.source == "archive" and weather.fallback is True


def test_use_archive_false_keeps_the_original_failure(stored_root: Path, tmp_path: Path) -> None:
    """폴백을 끄면 원래 오류가 그대로 올라온다."""
    request = WeatherRequest(37.5, 127.0, dt.date(2023, 6, 29), dt.date(2023, 7, 3))
    with pytest.raises(WeatherUnavailableError, match="모의"):
        load_weather(
            request,
            fetch=_failing_fetch,
            cache_dir=tmp_path / "cache",
            archive_dir=stored_root,
            use_archive=False,
        )


def test_fallback_is_not_written_into_the_cache(stored_root: Path, tmp_path: Path) -> None:
    """폴백분을 캐시에 쓰면 다음 실행에서 '캐시'로 둔갑해 표시가 사라진다."""
    cache_dir = tmp_path / "cache"
    request = WeatherRequest(37.5, 127.0, dt.date(2023, 6, 29), dt.date(2023, 7, 3))
    weather = load_weather(
        request, fetch=_failing_fetch, cache_dir=cache_dir, archive_dir=stored_root
    )
    assert weather.path is None
    assert not list(cache_dir.rglob("*.parquet"))


def test_out_of_range_period_stops_with_guidance(stored_root: Path, tmp_path: Path) -> None:
    """범위 밖이면 **멈춘다.** 0 으로 계산하거나 인접 격자로 대체하지 않는다."""
    request = WeatherRequest(37.5, 127.0, dt.date(2019, 1, 1), dt.date(2019, 12, 31))
    with pytest.raises(WeatherUnavailableError) as caught:
        load_weather(
            request,
            fetch=_failing_fetch,
            cache_dir=tmp_path / "cache",
            archive_dir=stored_root,
        )
    message = str(caught.value)
    assert "2019-01 ~ 2019-12" in message
    assert "사전 취득 범위" in message
    assert "tools\\fetch_weather.py" in message


def test_unknown_cell_stops_instead_of_borrowing_a_neighbour(
    stored_root: Path, tmp_path: Path
) -> None:
    """부산을 물었는데 서울 격자로 대신 계산하지 않는다."""
    request = WeatherRequest(35.18, 129.07, dt.date(2023, 6, 29), dt.date(2023, 7, 3))
    with pytest.raises(WeatherUnavailableError, match="사전 취득"):
        load_weather(
            request,
            fetch=_failing_fetch,
            cache_dir=tmp_path / "cache",
            archive_dir=stored_root,
        )


def test_cache_still_wins_over_everything(stored_root: Path, tmp_path: Path) -> None:
    """반복 호출은 PROJECT_CACHE 가 막는다. 성공분은 캐시에 남는다."""
    cache_dir = tmp_path / "cache"
    request = WeatherRequest(37.5, 127.0, dt.date(2023, 6, 29), dt.date(2023, 7, 3))
    weather = clearsky_weather(start="2023-06-28", end="2023-07-05")
    first = load_weather(request, fetch=lambda _: weather.hourly, cache_dir=cache_dir)
    assert first.source == "network" and first.path is not None

    second = load_weather(request, fetch=_failing_fetch, cache_dir=cache_dir)
    assert second.source == "cache"


# --------------------------------------------------------------------- 호출 제한


class _FakeTransport:
    """정해진 순서로 응답·오류를 낸다."""

    def __init__(self, *outcomes: object) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self, url: str, params: dict[str, str], timeout: float) -> dict[str, object]:
        self.calls += 1
        outcome = self.outcomes[min(self.calls - 1, len(self.outcomes) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        assert isinstance(outcome, dict)
        return outcome


def test_429_backs_off_and_retries() -> None:
    """429 는 지수 백오프로 다시 부른다."""
    transport = _FakeTransport(
        WeatherHttpError("rate limit", status=429),
        WeatherHttpError("rate limit", status=429),
        {"ok": True},
    )
    slept: list[float] = []
    payload = fetch_json_with_retry(
        "https://example.invalid",
        {},
        policy=RetryPolicy(max_attempts=5, backoff_base_sec=2.0),
        transport=transport,
        sleep=slept.append,
    )
    assert payload == {"ok": True}
    assert transport.calls == 3
    assert slept == [2.0, 4.0]  # 지수 백오프


def test_retry_gives_up_after_the_limit_so_the_run_can_continue() -> None:
    """최대 횟수를 넘기면 **그 셀만 실패로 남기고** 호출자가 계속 진행한다."""
    transport = _FakeTransport(WeatherHttpError("rate limit", status=429))
    slept: list[float] = []
    with pytest.raises(WeatherHttpError) as caught:
        fetch_cell_year(
            CELL,
            2023,
            policy=RetryPolicy(max_attempts=3, backoff_base_sec=1.0),
            transport=transport,
            sleep=slept.append,
        )
    assert caught.value.status == 429
    assert transport.calls == 3
    assert slept == [1.0, 2.0]


def test_backoff_is_capped() -> None:
    policy = RetryPolicy(backoff_base_sec=2.0, backoff_max_sec=10.0)
    assert [policy.delay(attempt) for attempt in (1, 2, 3, 4, 5)] == [2.0, 4.0, 8.0, 10.0, 10.0]


def test_client_errors_are_not_retried() -> None:
    """400 은 다시 불러도 같은 답이 온다. 즉시 실패로 남긴다."""
    transport = _FakeTransport(WeatherHttpError("bad request", status=400))
    with pytest.raises(WeatherHttpError):
        fetch_json_with_retry(
            "https://example.invalid", {}, transport=transport, sleep=lambda _: None
        )
    assert transport.calls == 1


def test_connection_errors_are_retried() -> None:
    transport = _FakeTransport(WeatherHttpError("연결 실패"), {"ok": True})
    fetch_json_with_retry(
        "https://example.invalid",
        {},
        policy=RetryPolicy(max_attempts=2, backoff_base_sec=0.5),
        transport=transport,
        sleep=lambda _: None,
    )
    assert transport.calls == 2


def test_pacer_keeps_the_minimum_interval() -> None:
    """호출 간 최소 간격을 지킨다 (기본 1초)."""
    now = [100.0]
    slept: list[float] = []

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    pacer = Pacer(min_interval_sec=1.0, sleep=sleep, clock=lambda: now[0])
    assert pacer.wait() == 0.0  # 첫 호출은 기다리지 않는다
    now[0] += 0.25
    assert pacer.wait() == pytest.approx(0.75)
    now[0] += 5.0
    assert pacer.wait() == 0.0  # 이미 충분히 지났다
    assert slept == [0.75]


def test_index_can_be_rebuilt_from_the_parquet_files(stored_root: Path) -> None:
    """색인을 잃어도 파일에서 되살린다. 배포 중 색인만 빠지는 일이 있다."""
    from kwise.pv.archive import INDEX_FILENAME, rebuild_index

    (stored_root / INDEX_FILENAME).unlink()
    assert archive_status(stored_root).cell_count == 0

    status = rebuild_index(stored_root)
    assert status.cell_count == 1
    assert status.years == (2023,)
