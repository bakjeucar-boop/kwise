from __future__ import annotations

import os
import math
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd
import pvlib

from pvlib.location import Location

try:
    from pvlib.irradiance import get_total_irradiance as gettotalirradiance
except Exception:
    from pvlib.irradiance import gettotalirradiance  # type: ignore

try:
    from pvlib.temperature import sapm_cell as sapmcell
except Exception:
    from pvlib.temperature import sapmcell  # type: ignore

try:
    from pvlib.temperature import TEMPERATURE_MODEL_PARAMETERS as TEMPERATUREMODELPARAMETERS
except Exception:
    from pvlib.temperature import TEMPERATUREMODELPARAMETERS  # type: ignore

try:
    from pvlib.iotools import get_pvgis_horizon as getpvgishorizon
except Exception:
    from pvlib.iotools import getpvgishorizon  # type: ignore


# ---- defaults (원본 그대로) ----
MODULEPDCSTCW = 640
MODULECOUNT = 20
INVACRATEDWPER = 3500
INVERTERCOUNT = 5
ETAINVNOM = 0.96
SURFACETILT = 7.2
SURFACEAZIMUTH = 159
ALBEDO = 0.12

GAMMAPDC = -0.0035  # (원본 주석: PVWatts gammapdc is 1/C)

LOSSPARAMS = dict(
    soiling=1.0,
    shading=0.0,
    snow=0.0,
    mismatch=1.0,
    wiring=1.0,
    connections=0.5,
    lid=1.0,
    nameplate_rating=1.0,
    age=0.0,
    availability=0.0,
)

DEFAULTOBSTACLE = dict(centerazdeg=200.0, distm=123.0, heightm=60.0, widthm=122.0)

HORIZONMODE = "pvgis"  # pvgis | csv | flat
HORIZONCSVPATH = "horizonprofile.csv"

ADJUSTGHIWHENDNIBLOCKED = True

PLANNEDAVAILABILITY = 1.0
SNOWFALLTHRESHOLDMMPERH = 0.5
SNOWLOSSONEVENT = 0.4
SNOWRECOVERYDAYS = 2.0


@contextmanager
def withoutproxyenv():
    proxykeys = [
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
    ]
    oldvalues = {key: os.environ.get(key) for key in proxykeys}
    for key in proxykeys:
        os.environ.pop(key, None)
    try:
        yield
    finally:
        for key, value in oldvalues.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _first_col(df: pd.DataFrame, candidates: List[str]) -> str:
    for c in candidates:
        if c in df.columns:
            return c
    raise KeyError(f"None of columns exist: {candidates}. Existing: {list(df.columns)[:30]} ...")


# ---- horizon ----
def obstacleazrange(centerazdeg, distm, widthm):
    halfwidthangle = math.degrees(math.atan2(widthm / 2.0, distm))
    azstart = (centerazdeg - halfwidthangle) % 360
    azend = (centerazdeg + halfwidthangle) % 360
    return azstart, azend


def obstacleelevationdeg(distm, heightm):
    return math.degrees(math.atan2(heightm, distm))


def applyobstacletohorizon(fullhorizondeg: np.ndarray, obstacle: Dict[str, Any]) -> np.ndarray:
    center = float(obstacle["centerazdeg"])
    dist = float(obstacle["distm"])
    height = float(obstacle["heightm"])
    width = float(obstacle["widthm"])
    azstart, azend = obstacleazrange(center, dist, width)
    elev = obstacleelevationdeg(dist, height)
    az = np.arange(360)
    if azstart <= azend:
        mask = (az >= azstart) & (az <= azend)
    else:
        mask = (az >= azstart) | (az <= azend)
    fullhorizondeg[mask] = np.maximum(fullhorizondeg[mask], elev)
    return fullhorizondeg


def loadhorizonbase(lat, lon) -> pd.DataFrame:
    if HORIZONMODE == "flat":
        return pd.DataFrame({"azimuthdeg": [0.0], "elevationdeg": [0.0]})

    if HORIZONMODE == "csv":
        df = pd.read_csv(HORIZONCSVPATH, encoding="utf-8-sig")
        df.columns = [c.strip().lower() for c in df.columns]
        df = df.rename(columns={"azimuth": "azimuthdeg", "elevation": "elevationdeg"})
        df["azimuthdeg"] = pd.to_numeric(df["azimuthdeg"], errors="coerce")
        df["elevationdeg"] = pd.to_numeric(df["elevationdeg"], errors="coerce")
        df = df.dropna().sort_values("azimuthdeg")
        return df[["azimuthdeg", "elevationdeg"]]

    try:
        with withoutproxyenv():
            horizondata, _ = getpvgishorizon(lat, lon)
    except Exception:
        try:
            horizondata, _ = getpvgishorizon(lat, lon)
        except Exception:
            return pd.DataFrame({"azimuthdeg": [0.0], "elevationdeg": [0.0]})
    if isinstance(horizondata, pd.Series):
        df = pd.DataFrame({"azimuthdeg": horizondata.index.astype(float), "elevationdeg": horizondata.values.astype(float)})
    else:
        df = horizondata.copy()
        df = df.rename(columns={"azimuth": "azimuthdeg", "elevation": "elevationdeg"})
        if "azimuthdeg" not in df.columns:
            df["azimuthdeg"] = df.index.astype(float)
        df = df.sort_values("azimuthdeg")
    return df[["azimuthdeg", "elevationdeg"]]


def buildhorizonprofile(lat, lon, obstacles: List[Dict[str, Any]]) -> np.ndarray:
    base = loadhorizonbase(lat, lon)
    azgrid = np.arange(360, dtype=float)
    baseelev = np.interp(azgrid, base["azimuthdeg"].values, base["elevationdeg"].values, period=360)
    full = baseelev.astype(float)
    for obs in (obstacles or []):
        full = applyobstacletohorizon(full, obs)
    return full


# ---- snow / pv ----
def buildsnowlossfactor(index, snowfallmmperh) -> pd.Series:
    if snowfallmmperh is None:
        return pd.Series(1.0, index=index)
    snowfall = pd.to_numeric(snowfallmmperh, errors="coerce").fillna(0.0)
    snowevent = snowfall >= SNOWFALLTHRESHOLDMMPERH
    factor = pd.Series(1.0, index=index)
    currentfactor = 1.0
    recoveryhours = SNOWRECOVERYDAYS * 24.0
    for t in index:
        if snowevent.get(t, False):
            currentfactor = 1.0 - SNOWLOSSONEVENT
        else:
            if currentfactor < 1.0:
                currentfactor = min(1.0, currentfactor + (SNOWLOSSONEVENT / max(recoveryhours, 1.0)))
        factor[t] = currentfactor
    return factor


@dataclass
class GeneratorConfig:
    name: str
    modulepdcstcw: float = MODULEPDCSTCW
    modulecount: int = MODULECOUNT
    invacratedwper: float = INVACRATEDWPER
    invertercount: int = INVERTERCOUNT
    etainvnom: float = ETAINVNOM
    surfacetilt: float = SURFACETILT
    surfaceazimuth: float = SURFACEAZIMUTH
    albedo: float = ALBEDO
    mounting: str = "Open rack"  # "Open rack" | "Close mount"
    facetype: str = "Bifacial"   # "Monofacial" | "Bifacial"
    bifacialityfactorpct: float = 70.0
    gammapctperc: float = -0.35
    plannedavailability: float = PLANNEDAVAILABILITY
    obstacles: List[Dict[str, Any]] = field(default_factory=list)
    losssettings: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def pdc0totalw(self) -> float:
        return float(self.modulepdcstcw) * float(self.modulecount)

    def invactotalw(self) -> float:
        return float(self.invacratedwper) * float(self.invertercount)

    def invpdc0totalw(self) -> float:
        eta = float(self.etainvnom) if float(self.etainvnom) else ETAINVNOM
        invpdc0wper = float(self.invacratedwper) / eta
        return invpdc0wper * float(self.invertercount)

    def gammapdc1perc(self) -> float:
        return float(self.gammapctperc) / 100.0

    def bifacialityfactor(self) -> float:
        if str(self.facetype).strip().lower() != "bifacial":
            return 0.0
        try:
            v = float(self.bifacialityfactorpct)
        except Exception:
            v = 70.0
        v = max(0.0, min(100.0, v))
        return v / 100.0

    def tempparameterset(self) -> str:
        m = str(self.mounting).strip().lower()
        return "close_mount_glass_glass" if m == "close mount" else "open_rack_glass_glass"


def defaultobstacles() -> List[Dict[str, Any]]:
    return [
        {"enabled": True, **DEFAULTOBSTACLE},
        {"enabled": False, "centerazdeg": None, "distm": None, "heightm": None, "widthm": None},
        {"enabled": False, "centerazdeg": None, "distm": None, "heightm": None, "widthm": None},
        {"enabled": False, "centerazdeg": None, "distm": None, "heightm": None, "widthm": None},
        {"enabled": False, "centerazdeg": None, "distm": None, "heightm": None, "widthm": None},
    ]


def defaultlosssettings() -> Dict[str, Dict[str, Any]]:
    keys = ["soiling", "mismatch", "wiring", "connections", "lid", "nameplate_rating", "age", "availability"]
    return {k: {"enabled": True, "value": float(LOSSPARAMS.get(k, 0.0))} for k in keys}


def lossparamsforgenerator(gen: GeneratorConfig) -> Dict[str, float]:
    params = {"shading": 0.0, "snow": 0.0}
    for k, item in (gen.losssettings or {}).items():
        en = bool(item.get("enabled", True))
        v = float(item.get("value", 0.0))
        params[k] = v if en else 0.0
    for k, v in (LOSSPARAMS or {}).items():
        if k in ("shading", "snow"):
            continue
        if k not in params:
            params[k] = float(v)
    return params


def computepvforgenerator(
    weatherhourly: pd.DataFrame,
    gen: GeneratorConfig,
    obstaclesenabled: List[Dict[str, Any]],
    lossesparams: Dict[str, float],
    lat: float,
    lon: float,
    tz: str,
    altitudem: float,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    horizon360 = buildhorizonprofile(lat, lon, obstaclesenabled)
    location = Location(latitude=float(lat), longitude=float(lon), tz=tz, altitude=float(altitudem))
    solarpos = location.get_solarposition(weatherhourly.index)

    solaraz = solarpos["azimuth"].astype(float).values
    solarel = solarpos["apparent_elevation"].astype(float).values

    azgrid = np.arange(360, dtype=float)
    horizonattime = np.interp(solaraz % 360, azgrid, horizon360, period=360)

    isnight = solarel <= 0
    isshaded = solarel < horizonattime

    dni_col = _first_col(weatherhourly, ["direct_normal_irradiance", "directnormalirradiance"])
    ghi_col = _first_col(weatherhourly, ["shortwave_radiation", "shortwaveradiation"])
    dhi_col = _first_col(weatherhourly, ["diffuse_radiation", "diffuseradiation"])
    temp_col = _first_col(weatherhourly, ["temperature_2m", "temperature2m"])
    wind_col = _first_col(weatherhourly, ["wind_speed_10m", "windspeed_10m", "windspeed10m"])

    dni = weatherhourly[dni_col].astype(float)
    ghi = weatherhourly[ghi_col].astype(float)
    dhi = weatherhourly[dhi_col].astype(float)

    dniadj = dni.where(~isshaded, 0.0).where(~isnight, 0.0)
    ghiadj = ghi.where(~isnight, 0.0)
    dhiadj = dhi.where(~isnight, 0.0)

    if ADJUSTGHIWHENDNIBLOCKED:
        ghirepl = np.minimum(ghiadj.values, dhiadj.values)
        ghiadj = pd.Series(np.where(dniadj.values == 0.0, ghirepl, ghiadj.values), index=weatherhourly.index)

    poa = gettotalirradiance(
        surface_tilt=float(gen.surfacetilt),
        surface_azimuth=float(gen.surfaceazimuth),
        solar_zenith=solarpos["apparent_zenith"],
        solar_azimuth=solarpos["azimuth"],
        dni=dniadj,
        ghi=ghiadj,
        dhi=dhiadj,
        albedo=float(gen.albedo),
    )

    totalirrad = poa["poa_global"] + (poa.get("poa_ground_diffuse", 0.0) * gen.bifacialityfactor())

    tset = gen.tempparameterset()
    if tset not in TEMPERATUREMODELPARAMETERS["sapm"]:
        raise ValueError(f"Unknown temperature parameter set: {tset}")
    tempparams = TEMPERATUREMODELPARAMETERS["sapm"][tset]

    windmps = weatherhourly[wind_col] / 3.6
    celltemp = sapmcell(
        totalirrad,
        weatherhourly[temp_col],
        windmps,
        tempparams["a"],
        tempparams["b"],
        tempparams["deltaT"],
    )

    dcw = pvlib.pvsystem.pvwatts_dc(totalirrad, celltemp, gen.pdc0totalw(), gamma_pdc=gen.gammapdc1perc())
    acw = pvlib.inverter.pvwatts(dcw, gen.invpdc0totalw())

    totallosspct = pvlib.pvsystem.pvwatts_losses(**(lossesparams or {}))
    lossfactor = (100.0 - float(totallosspct)) / 100.0
    acwafterlosses = (acw * lossfactor).clip(lower=0)

    snowfactor = buildsnowlossfactor(weatherhourly.index, weatherhourly.get("snowfall"))
    acwafterlosses *= snowfactor.values
    acwafterlosses *= float(gen.plannedavailability)

    dthours = weatherhourly.index.to_series().diff().dt.total_seconds().div(3600).fillna(1.0)
    ackwh = (acwafterlosses / 1000.0) * dthours

    hourly = pd.DataFrame(
        dict(
            horizonelevdeg=horizonattime,
            isshaded=isshaded,
            dniadj=dniadj,
            ghiadj=ghiadj,
            dhiadj=dhiadj,
            snowfactor=snowfactor,
            acpowerw=acwafterlosses,
            generationkwh=ackwh,
        ),
        index=weatherhourly.index,
    )
    daily = hourly["generationkwh"].resample("D").sum().to_frame(name="dailygenerationkwh")
    return hourly, daily
