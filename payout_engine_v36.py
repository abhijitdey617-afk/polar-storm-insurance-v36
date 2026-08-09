"""
airline_polar_reroute_parametric_engine_v3_6.py

V3.6 calculation engine for Airline Polar Reroute Parametric Insurance.

Main update from V3.5:
    Added airport master table support for automatic coordinate lookup.
    The engine first checks explicit SOV coordinates, then airport_master.csv,
    then the small built-in fallback dictionary.

Production formula:
    FlightLoss = BaseCost * PFU_TierMultiplier * RouteExposure

Where:
    BaseCost       = route-level contractual reroute_cost_usd from SOV
    TierMultiplier = PFU tier payout factor
    RouteExposure  = PolarHours / FlightHours

Recommended SOV format:

    REQUIRED FIELDS
    ---------------
    flight_id
    origin
    destination
    reroute_cost_usd

    OPTIONAL FIELDS
    ---------------
    airline_id
    route_name
    flight_number
    scheduled_departure_utc
    flight_status
    duration_hours
    polar_hours
    route_exposure
    origin_latitude
    origin_longitude
    destination_latitude
    destination_longitude

Airport master table:
    The engine automatically looks for airport_master.csv in the same folder as this
    script. Required columns are flexible, but recommended columns are:
        iata, icao, airport_name, latitude, longitude, country, city

    Lookup order for coordinates:
        1. Explicit SOV coordinates
        2. airport_master.csv using origin/destination IATA or ICAO
        3. Built-in fallback AIRPORT_COORDINATES dictionary

Dynamic exposure and duration logic:
    1. If duration_hours is supplied, use it.
    2. Else derive duration_hours from great-circle distance / cruise speed, with a block-time factor.
    3. If route_exposure is supplied, use it.
    4. Else if polar_hours is supplied, derive route_exposure = polar_hours / duration_hours.
    5. Else derive polar_hours automatically from origin/destination great-circle geometry.
    6. If polar_hours = 0, route_exposure = 0 and the flight has no claim contribution.

Important:
    - Flight status is informational only and does not affect claim eligibility.
    - The same formula is used for stochastic pricing and live claim settlement.
    - This is a parametric model. It is not intended to reconstruct actual airline loss.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict, Iterable, List, Optional, Tuple
from pathlib import Path
import math
import numpy as np
import pandas as pd


# =============================================================================
# 1. CONFIGURATION
# =============================================================================

PFU_TRIGGER_DEFAULT = 1000.0
MIN_DURATION_HOURS_DEFAULT = 6.0
DEFAULT_ROUTE_EXPOSURE = 0.0
MIN_ROUTE_EXPOSURE_DEFAULT = 0.0
POLAR_LATITUDE_THRESHOLD_DEFAULT = 60.0
GREAT_CIRCLE_STEPS_DEFAULT = 240
EARTH_RADIUS_KM = 6371.0088
DEFAULT_CRUISE_SPEED_KMPH = 850.0
DEFAULT_BLOCK_TIME_FACTOR = 1.10
MIN_DERIVED_DURATION_HOURS = 0.25
DEFAULT_AIRPORT_MASTER_FILE = "airport_master.csv"

PFU_TIER_CONFIG = {
    "NO_TRIGGER": {"min_pfu": 0.0, "max_pfu": 999.999999, "multiplier": 0.00},
    "TIER_1": {"min_pfu": 1000.0, "max_pfu": 2299.999999, "multiplier": 0.25},
    "TIER_2": {"min_pfu": 2300.0, "max_pfu": 9999.999999, "multiplier": 0.60},
    "TIER_3": {"min_pfu": 10000.0, "max_pfu": float("inf"), "multiplier": 1.00},
}

# Demo/common coordinates. Production should use a full airport master table.
AIRPORT_COORDINATES = {
    "JFK": (40.6413, -73.7781), "ORD": (41.9742, -87.9073),
    "SFO": (37.6213, -122.3790), "LAX": (33.9416, -118.4085),
    "SEA": (47.4502, -122.3088), "HEL": (60.3172, 24.9633),
    "CPH": (55.6181, 12.6561), "ARN": (59.6498, 17.9238),
    "OSL": (60.1976, 11.1004), "KEF": (63.9850, -22.6056),
    "LHR": (51.4700, -0.4543), "CDG": (49.0097, 2.5479),
    "FRA": (50.0379, 8.5622), "AMS": (52.3105, 4.7683),
    "HND": (35.5494, 139.7798), "NRT": (35.7719, 140.3929),
    "ICN": (37.4602, 126.4407), "HKG": (22.3080, 113.9185),
    "DEL": (28.5562, 77.1000), "DXB": (25.2532, 55.3657),
    "DOH": (25.2731, 51.6081), "SIN": (1.3644, 103.9915),
    "YYZ": (43.6777, -79.6248), "YVR": (49.1967, -123.1815),
    "REK": (64.1355, -21.8954), "BOS": (42.3656, -71.0096),
}

REQUIRED_SOV_FIELDS = ["flight_id", "origin", "destination", "reroute_cost_usd"]
OPTIONAL_SOV_FIELDS = [
    "airline_id", "route_name", "flight_number", "scheduled_departure_utc", "flight_status",
    "duration_hours", "polar_hours", "route_exposure",
    "origin_latitude", "origin_longitude", "destination_latitude", "destination_longitude",
]


# =============================================================================
# 2. DATA STRUCTURES
# =============================================================================

@dataclass
class PolicyTerms:
    per_event_limit: float
    event_deductible: float
    annual_aggregate: float
    remaining_annual_aggregate: Optional[float] = None

    def __post_init__(self) -> None:
        if self.remaining_annual_aggregate is None:
            self.remaining_annual_aggregate = self.annual_aggregate


@dataclass
class LivePFUEvent:
    event_id: str
    event_start_utc: str
    event_end_utc: str
    peak_pfu: float
    duration_hours: float
    qualified: bool
    qualification_reason: str


@dataclass
class FlightCalculationResult:
    flight_id: str
    route_name: str
    airline_id: str
    origin: str
    destination: str
    scheduled_departure_utc: Optional[str]
    flight_status: Optional[str]
    base_cost: float
    cost_source: str
    severity_tier: str
    tier_multiplier: float
    duration_hours: float
    duration_source: str
    polar_hours: float
    route_exposure: float
    route_exposure_source: str
    flight_loss: float
    settlement_basis: str


@dataclass
class EventCalculationResult:
    event_id: str
    peak_pfu: float
    severity_tier: str
    tier_multiplier: float
    gross_event_loss: float
    after_deductible: float
    event_payout_before_aggregate: float
    final_event_payout: float
    remaining_aggregate_after_event: float
    eligible_flight_count: int
    flight_results: List[FlightCalculationResult]


# =============================================================================
# 3. SOV SCHEMA / VALIDATION
# =============================================================================

def get_sov_schema() -> pd.DataFrame:
    rows = []
    for field in REQUIRED_SOV_FIELDS:
        rows.append({"field": field, "required": True})
    for field in OPTIONAL_SOV_FIELDS:
        rows.append({"field": field, "required": False})
    return pd.DataFrame(rows)


def validate_minimum_sov(sov: pd.DataFrame) -> None:
    missing = [c for c in REQUIRED_SOV_FIELDS if c not in sov.columns]
    if missing:
        raise KeyError(f"Missing required SOV columns: {missing}")


# =============================================================================
# 4. PFU TIER FUNCTIONS
# =============================================================================

def get_pfu_tier(peak_pfu: float) -> str:
    pfu = float(peak_pfu)
    for tier_name, cfg in PFU_TIER_CONFIG.items():
        if cfg["min_pfu"] <= pfu <= cfg["max_pfu"]:
            return tier_name
    return "NO_TRIGGER"


def tier_multiplier_pfu(peak_pfu: float) -> float:
    return float(PFU_TIER_CONFIG[get_pfu_tier(peak_pfu)]["multiplier"])


# =============================================================================
# 5. LIVE PFU EVENT EVALUATION
# =============================================================================

def evaluate_live_pfu_event(
    pfu_readings: pd.DataFrame,
    timestamp_col: str = "timestamp_utc",
    pfu_col: str = "pfu",
    event_id: str = "LIVE_EVENT",
    pfu_trigger: float = PFU_TRIGGER_DEFAULT,
    min_duration_hours: float = MIN_DURATION_HOURS_DEFAULT,
) -> LivePFUEvent:
    if pfu_readings.empty:
        return LivePFUEvent(event_id, "", "", 0.0, 0.0, False, "no_readings")

    df = pfu_readings.copy()
    if timestamp_col not in df.columns or pfu_col not in df.columns:
        raise KeyError(f"Expected columns: {timestamp_col}, {pfu_col}")

    df[timestamp_col] = pd.to_datetime(df[timestamp_col], utc=True, errors="coerce")
    df[pfu_col] = pd.to_numeric(df[pfu_col], errors="coerce")
    df = df.dropna(subset=[timestamp_col, pfu_col]).sort_values(timestamp_col)

    if df.empty:
        return LivePFUEvent(event_id, "", "", 0.0, 0.0, False, "invalid_readings")

    above = df[df[pfu_col] >= pfu_trigger]
    peak_pfu = float(df[pfu_col].max())

    if above.empty:
        return LivePFUEvent(
            event_id=event_id,
            event_start_utc=str(df[timestamp_col].min()),
            event_end_utc=str(df[timestamp_col].max()),
            peak_pfu=peak_pfu,
            duration_hours=0.0,
            qualified=False,
            qualification_reason="pfu_below_trigger",
        )

    intervals = df[timestamp_col].diff().dt.total_seconds().dropna() / 3600.0
    median_interval = float(intervals.median()) if not intervals.empty else 1.0
    median_interval = max(median_interval, 1.0 / 12.0)
    duration_hours = float(len(above) * median_interval)
    qualified = bool(peak_pfu >= pfu_trigger and duration_hours >= min_duration_hours)

    return LivePFUEvent(
        event_id=event_id,
        event_start_utc=str(above[timestamp_col].min()),
        event_end_utc=str(above[timestamp_col].max()),
        peak_pfu=peak_pfu,
        duration_hours=duration_hours,
        qualified=qualified,
        qualification_reason="qualified" if qualified else "duration_below_minimum",
    )


# =============================================================================
# 6. COORDINATES, DISTANCE, DURATION, POLAR HOURS
# =============================================================================

def _default_airport_master_path() -> Path:
    """Return the default airport master path next to this script."""
    try:
        return Path(__file__).resolve().with_name(DEFAULT_AIRPORT_MASTER_FILE)
    except NameError:
        return Path(DEFAULT_AIRPORT_MASTER_FILE)


def load_airport_master(airport_master_path: Optional[str] = None) -> pd.DataFrame:
    """
    Load airport master table for coordinate lookup.

    Recommended columns:
        iata, icao, airport_name, latitude, longitude, country, city

    Behaviour:
        - If airport_master_path is provided, use that file.
        - Else look for airport_master.csv in the same folder as this script.
        - If file does not exist, return an empty dataframe and use built-in fallback.
    """
    path = Path(airport_master_path) if airport_master_path else _default_airport_master_path()
    if not path.exists():
        return pd.DataFrame()

    master = pd.read_csv(path)
    master.columns = [str(c).strip().lower() for c in master.columns]

    required_any = {"iata", "icao"}
    if not required_any.intersection(set(master.columns)):
        raise KeyError("airport_master.csv must contain at least one of: iata, icao")
    if "latitude" not in master.columns or "longitude" not in master.columns:
        raise KeyError("airport_master.csv must contain latitude and longitude columns")

    for col in ["iata", "icao"]:
        if col in master.columns:
            master[col] = master[col].astype(str).str.upper().str.strip()

    master["latitude"] = pd.to_numeric(master["latitude"], errors="coerce")
    master["longitude"] = pd.to_numeric(master["longitude"], errors="coerce")
    master = master.dropna(subset=["latitude", "longitude"])
    return master


def lookup_airport_coordinates(code: str, airport_master_path: Optional[str] = None) -> Tuple[Optional[Tuple[float, float]], str]:
    """
    Lookup airport coordinates by IATA or ICAO.

    Priority:
        1. airport_master.csv
        2. built-in AIRPORT_COORDINATES fallback dictionary
    """
    airport_code = str(code or "").strip().upper()
    if not airport_code:
        return None, "MISSING_AIRPORT_CODE"

    master = load_airport_master(airport_master_path)
    if not master.empty:
        matches = []
        if "iata" in master.columns:
            matches.append(master[master["iata"] == airport_code])
        if "icao" in master.columns:
            matches.append(master[master["icao"] == airport_code])
        if matches:
            matched = pd.concat(matches, ignore_index=True)
            if not matched.empty:
                row = matched.iloc[0]
                return (float(row["latitude"]), float(row["longitude"])), "AIRPORT_MASTER_CSV"

    if airport_code in AIRPORT_COORDINATES:
        return AIRPORT_COORDINATES[airport_code], "BUILT_IN_FALLBACK"

    return None, "NOT_FOUND"


def resolve_airport_coordinates(
    row: pd.Series,
    airport_master_path: Optional[str] = None,
) -> Tuple[Tuple[float, float], Tuple[float, float], str]:
    """
    Resolve origin/destination coordinates.

    Priority:
        1. Explicit origin/destination lat-lon columns in SOV
        2. airport_master.csv lookup by IATA or ICAO code
        3. Built-in AIRPORT_COORDINATES fallback dictionary
    """
    explicit_cols = ["origin_latitude", "origin_longitude", "destination_latitude", "destination_longitude"]
    if all(c in row.index and pd.notna(row.get(c)) for c in explicit_cols):
        origin = (float(row["origin_latitude"]), float(row["origin_longitude"]))
        dest = (float(row["destination_latitude"]), float(row["destination_longitude"]))
        return origin, dest, "SOV_EXPLICIT_COORDINATES"

    origin_code = str(row.get("origin", "")).strip().upper()
    dest_code = str(row.get("destination", "")).strip().upper()

    origin, origin_source = lookup_airport_coordinates(origin_code, airport_master_path)
    dest, dest_source = lookup_airport_coordinates(dest_code, airport_master_path)

    if origin is not None and dest is not None:
        if origin_source == "AIRPORT_MASTER_CSV" or dest_source == "AIRPORT_MASTER_CSV":
            source = "AIRPORT_MASTER_CSV"
        else:
            source = "BUILT_IN_FALLBACK"
        return origin, dest, source

    missing = []
    if origin is None:
        missing.append(f"origin={origin_code}")
    if dest is None:
        missing.append(f"destination={dest_code}")
    raise ValueError(
        "Could not resolve coordinates for " + ", ".join(missing) + ". "
        "Add the airport to airport_master.csv, provide IATA/ICAO code available in the master, "
        "or provide explicit origin_latitude/origin_longitude and destination_latitude/destination_longitude."
    )

def haversine_distance_km(origin: Tuple[float, float], destination: Tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, origin)
    lat2, lon2 = map(math.radians, destination)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2.0) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2.0) ** 2
    c = 2.0 * math.asin(min(1.0, math.sqrt(a)))
    return EARTH_RADIUS_KM * c


def derive_duration_hours_for_row(
    row: pd.Series,
    cruise_speed_kmph: float = DEFAULT_CRUISE_SPEED_KMPH,
    block_time_factor: float = DEFAULT_BLOCK_TIME_FACTOR,
) -> Tuple[float, str]:
    supplied_duration = row.get("duration_hours", np.nan)
    if pd.notna(supplied_duration) and float(supplied_duration) > 0:
        return float(supplied_duration), "SOV_DURATION_HOURS"

    origin, dest, coord_source = resolve_airport_coordinates(row)
    dist_km = haversine_distance_km(origin, dest)
    derived = (dist_km / cruise_speed_kmph) * block_time_factor
    derived = max(MIN_DERIVED_DURATION_HOURS, float(derived))
    return derived, f"AUTO_DISTANCE_SPEED_{coord_source}"


def _latlon_to_vector(lat_deg: float, lon_deg: float) -> np.ndarray:
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    return np.array([math.cos(lat) * math.cos(lon), math.cos(lat) * math.sin(lon), math.sin(lat)], dtype=float)


def _vector_to_latlon(vec: np.ndarray) -> Tuple[float, float]:
    vec = vec / np.linalg.norm(vec)
    lat = math.degrees(math.asin(vec[2]))
    lon = math.degrees(math.atan2(vec[1], vec[0]))
    return lat, lon


def great_circle_latitudes(
    origin_lat: float, origin_lon: float, dest_lat: float, dest_lon: float,
    steps: int = GREAT_CIRCLE_STEPS_DEFAULT,
) -> np.ndarray:
    v0 = _latlon_to_vector(origin_lat, origin_lon)
    v1 = _latlon_to_vector(dest_lat, dest_lon)
    dot = float(np.clip(np.dot(v0, v1), -1.0, 1.0))
    omega = math.acos(dot)

    if abs(omega) < 1e-12:
        return np.array([origin_lat] * (steps + 1), dtype=float)

    lats = []
    for i in range(steps + 1):
        f = i / steps
        vec = (math.sin((1 - f) * omega) / math.sin(omega)) * v0 + (math.sin(f * omega) / math.sin(omega)) * v1
        lat, _ = _vector_to_latlon(vec)
        lats.append(lat)
    return np.array(lats, dtype=float)


def estimate_polar_hours_from_route(
    origin: Tuple[float, float], destination: Tuple[float, float], duration_hours: float,
    polar_latitude_threshold: float = POLAR_LATITUDE_THRESHOLD_DEFAULT,
    steps: int = GREAT_CIRCLE_STEPS_DEFAULT,
) -> float:
    if duration_hours is None or duration_hours <= 0:
        return 0.0
    lats = great_circle_latitudes(origin[0], origin[1], destination[0], destination[1], steps=steps)
    midpoint_lats = (lats[:-1] + lats[1:]) / 2.0
    polar_fraction = float((np.abs(midpoint_lats) >= polar_latitude_threshold).mean())
    return max(0.0, min(float(duration_hours), polar_fraction * float(duration_hours)))


def derive_polar_hours_for_row(
    row: pd.Series,
    duration_hours: float,
    polar_latitude_threshold: float = POLAR_LATITUDE_THRESHOLD_DEFAULT,
    steps: int = GREAT_CIRCLE_STEPS_DEFAULT,
) -> Tuple[float, str]:
    polar_hours = row.get("polar_hours", np.nan)
    if pd.notna(polar_hours) and float(polar_hours) >= 0:
        return float(polar_hours), "SOV_POLAR_HOURS"

    route_exposure = row.get("route_exposure", np.nan)
    if pd.notna(route_exposure) and duration_hours > 0:
        derived = max(0.0, min(float(duration_hours), float(route_exposure) * float(duration_hours)))
        return derived, "DERIVED_FROM_SOV_ROUTE_EXPOSURE"

    origin, dest, coord_source = resolve_airport_coordinates(row)
    derived = estimate_polar_hours_from_route(origin, dest, duration_hours, polar_latitude_threshold, steps)
    return derived, f"AUTO_GREAT_CIRCLE_{coord_source}"


def enrich_sov_route_exposure(
    sov: pd.DataFrame,
    polar_latitude_threshold: float = POLAR_LATITUDE_THRESHOLD_DEFAULT,
    steps: int = GREAT_CIRCLE_STEPS_DEFAULT,
) -> pd.DataFrame:
    validate_minimum_sov(sov)
    out = sov.copy()

    duration_values, duration_sources = [], []
    polar_values, exposure_values, exposure_sources = [], [], []

    for _, row in out.iterrows():
        duration, duration_source = derive_duration_hours_for_row(row)

        supplied_exposure = row.get("route_exposure", np.nan)
        supplied_polar_hours = row.get("polar_hours", np.nan)

        if pd.notna(supplied_exposure):
            exposure = max(0.0, min(1.0, float(supplied_exposure)))
            polar_hours = exposure * duration if pd.isna(supplied_polar_hours) else float(supplied_polar_hours)
            exposure_source = "SOV_ROUTE_EXPOSURE"
        else:
            polar_hours, exposure_source = derive_polar_hours_for_row(row, duration, polar_latitude_threshold, steps)
            polar_hours = max(0.0, min(duration, float(polar_hours)))
            exposure = polar_hours / duration if duration > 0 else 0.0

        duration_values.append(float(duration))
        duration_sources.append(duration_source)
        polar_values.append(float(polar_hours))
        exposure_values.append(max(0.0, min(1.0, float(exposure))))
        exposure_sources.append(exposure_source)

    out["duration_hours"] = duration_values
    out["duration_source"] = duration_sources
    out["polar_hours"] = polar_values
    out["route_exposure"] = exposure_values
    out["route_exposure_source"] = exposure_sources
    return out


# =============================================================================
# 7. SCHEDULE AND SOV MATCHING
# =============================================================================

def normalise_key(value) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip().upper()


def prepare_sov_for_matching(sov: pd.DataFrame) -> pd.DataFrame:
    out = sov.copy()
    if "flight_id" not in out.columns and "flight_number" in out.columns:
        out["flight_id"] = out["flight_number"]
    for col in ["flight_id", "flight_number", "route_name", "airline_id", "origin", "destination"]:
        out[f"_{col}_key"] = out[col].apply(normalise_key) if col in out.columns else ""
    return out


def prepare_schedule_for_matching(schedule: pd.DataFrame) -> pd.DataFrame:
    out = schedule.copy().reset_index(drop=False).rename(columns={"index": "_schedule_row_id"})
    if "flight_id" not in out.columns and "flight_number" in out.columns:
        out["flight_id"] = out["flight_number"]
    for col in ["flight_id", "flight_number", "route_name", "airline_id", "origin", "destination"]:
        out[f"_{col}_key"] = out[col].apply(normalise_key) if col in out.columns else ""
    return out


def match_scheduled_flights_to_sov(scheduled_flights: pd.DataFrame, sov: pd.DataFrame) -> pd.DataFrame:
    if scheduled_flights.empty:
        return pd.DataFrame()

    sov_prepped = prepare_sov_for_matching(enrich_sov_route_exposure(sov))
    sch_prepped = prepare_schedule_for_matching(scheduled_flights)

    match_results, used_ids = [], set()
    match_rules = [
        ["_airline_id_key", "_flight_id_key"],
        ["_airline_id_key", "_flight_number_key"],
        ["_airline_id_key", "_route_name_key"],
        ["_airline_id_key", "_origin_key", "_destination_key"],
        ["_flight_id_key"],
        ["_route_name_key"],
        ["_origin_key", "_destination_key"],
    ]

    for rule in match_rules:
        remaining = sch_prepped[~sch_prepped["_schedule_row_id"].isin(used_ids)]
        if remaining.empty:
            break
        left, right = remaining.copy(), sov_prepped.copy()
        for key in rule:
            left = left[left[key] != ""]
            right = right[right[key] != ""]
        if left.empty or right.empty:
            continue
        merged = left.merge(right, on=rule, suffixes=("_schedule", ""), how="inner")
        if not merged.empty:
            merged["match_rule"] = "+".join(rule)
            match_results.append(merged)
            used_ids.update(merged["_schedule_row_id"].tolist())

    if not match_results:
        return pd.DataFrame()

    matched = pd.concat(match_results, ignore_index=True)
    matched = matched.drop_duplicates(subset=["_schedule_row_id"], keep="first")

    if "flight_id_schedule" in matched.columns:
        matched["live_flight_id"] = matched["flight_id_schedule"]
    elif "flight_id" in matched.columns:
        matched["live_flight_id"] = matched["flight_id"]

    if "scheduled_departure_utc_schedule" in matched.columns:
        matched["scheduled_departure_utc"] = matched["scheduled_departure_utc_schedule"]

    if "flight_status_schedule" in matched.columns:
        matched["live_flight_status"] = matched["flight_status_schedule"]
    elif "flight_status" in matched.columns:
        matched["live_flight_status"] = matched["flight_status"]

    return matched


# =============================================================================
# 8. BASE COST AND PAYOUT ENGINE
# =============================================================================

def determine_base_cost(row: pd.Series) -> Tuple[float, str]:
    reroute_cost = row.get("reroute_cost_usd", np.nan)
    if pd.notna(reroute_cost) and float(reroute_cost) > 0:
        return float(reroute_cost), "SOV_EXPLICIT_REROUTE_COST"
    raise ValueError("Missing reroute_cost_usd. V3.6 requires route-level base cost in the SOV.")


def calculate_flight_loss(
    row: pd.Series,
    peak_pfu: float,
    min_route_exposure: float = MIN_ROUTE_EXPOSURE_DEFAULT,
    settlement_basis: str = "SCHEDULED_EXPOSED_FLIGHT",
) -> Optional[FlightCalculationResult]:
    base_cost, cost_source = determine_base_cost(row)
    tier = get_pfu_tier(peak_pfu)
    multiplier = tier_multiplier_pfu(peak_pfu)

    route_exposure = float(row.get("route_exposure", DEFAULT_ROUTE_EXPOSURE) or DEFAULT_ROUTE_EXPOSURE)
    route_exposure = max(0.0, min(1.0, route_exposure))
    if route_exposure <= min_route_exposure:
        return None

    duration_hours = float(row.get("duration_hours", 0.0) or 0.0)
    duration_source = str(row.get("duration_source", "UNKNOWN"))
    polar_hours = float(row.get("polar_hours", 0.0) or 0.0)
    exposure_source = str(row.get("route_exposure_source", "UNKNOWN"))
    flight_loss = base_cost * multiplier * route_exposure

    flight_id = str(row.get("live_flight_id", row.get("flight_id", row.get("flight_number", "UNKNOWN_FLIGHT"))))
    route_name = str(row.get("route_name", f"{row.get('origin', '')}-{row.get('destination', '')}"))
    airline_id = str(row.get("airline_id", row.get("airline_id_schedule", "UNKNOWN_AIRLINE")))
    origin = str(row.get("origin", row.get("origin_schedule", "")))
    destination = str(row.get("destination", row.get("destination_schedule", "")))
    scheduled_departure = row.get("scheduled_departure_utc", row.get("scheduled_departure_utc_schedule", None))
    flight_status = row.get("live_flight_status", row.get("flight_status", row.get("flight_status_schedule", None)))

    return FlightCalculationResult(
        flight_id=flight_id,
        route_name=route_name,
        airline_id=airline_id,
        origin=origin,
        destination=destination,
        scheduled_departure_utc=str(scheduled_departure) if scheduled_departure is not None else None,
        flight_status=str(flight_status) if flight_status is not None else None,
        base_cost=base_cost,
        cost_source=cost_source,
        severity_tier=tier,
        tier_multiplier=multiplier,
        duration_hours=duration_hours,
        duration_source=duration_source,
        polar_hours=polar_hours,
        route_exposure=route_exposure,
        route_exposure_source=exposure_source,
        flight_loss=flight_loss,
        settlement_basis=settlement_basis,
    )


def calculate_event_payout(
    sov_rows: pd.DataFrame,
    peak_pfu: float,
    policy: PolicyTerms,
    event_id: str = "EVENT_001",
    min_route_exposure: float = MIN_ROUTE_EXPOSURE_DEFAULT,
    settlement_basis: str = "SCHEDULED_EXPOSED_FLIGHT",
) -> EventCalculationResult:
    flight_results: List[FlightCalculationResult] = []
    gross_event_loss = 0.0
    
    if 'route_exposure_source' not in sov_rows.columns:
        eligible_rows = enrich_sov_route_exposure(sov_rows)
    else:
        eligible_rows = sov_rows

    for _, row in eligible_rows.iterrows():
        result = calculate_flight_loss(row, peak_pfu, min_route_exposure, settlement_basis)
        if result is None:
            continue
        flight_results.append(result)
        gross_event_loss += result.flight_loss

    after_deductible = max(0.0, gross_event_loss - policy.event_deductible)
    event_payout_before_aggregate = min(after_deductible, policy.per_event_limit)
    final_event_payout = min(event_payout_before_aggregate, float(policy.remaining_annual_aggregate))
    remaining_after = max(0.0, float(policy.remaining_annual_aggregate) - final_event_payout)

    tier = get_pfu_tier(peak_pfu)
    multiplier = tier_multiplier_pfu(peak_pfu)
    return EventCalculationResult(
        event_id=event_id,
        peak_pfu=peak_pfu,
        severity_tier=tier,
        tier_multiplier=multiplier,
        gross_event_loss=gross_event_loss,
        after_deductible=after_deductible,
        event_payout_before_aggregate=event_payout_before_aggregate,
        final_event_payout=final_event_payout,
        remaining_aggregate_after_event=remaining_after,
        eligible_flight_count=len(flight_results),
        flight_results=flight_results,
    )


def calculate_live_claim_from_scheduled_flights(
    live_event: LivePFUEvent,
    scheduled_flights: pd.DataFrame,
    sov: pd.DataFrame,
    policy: PolicyTerms,
    require_qualified_event: bool = True,
    min_route_exposure: float = MIN_ROUTE_EXPOSURE_DEFAULT,
) -> EventCalculationResult:
    if require_qualified_event and not live_event.qualified:
        return EventCalculationResult(live_event.event_id, live_event.peak_pfu, get_pfu_tier(live_event.peak_pfu),
                                      tier_multiplier_pfu(live_event.peak_pfu), 0.0, 0.0, 0.0, 0.0,
                                      float(policy.remaining_annual_aggregate), 0, [])

    matched = match_scheduled_flights_to_sov(scheduled_flights, sov)
    if matched.empty:
        return EventCalculationResult(live_event.event_id, live_event.peak_pfu, get_pfu_tier(live_event.peak_pfu),
                                      tier_multiplier_pfu(live_event.peak_pfu), 0.0, 0.0, 0.0, 0.0,
                                      float(policy.remaining_annual_aggregate), 0, [])

    return calculate_event_payout(
        sov_rows=matched,
        peak_pfu=live_event.peak_pfu,
        policy=policy,
        event_id=live_event.event_id,
        min_route_exposure=min_route_exposure,
        settlement_basis="LIVE_SCHEDULED_EXPOSED_FLIGHT_STATUS_INFORMATIONAL_ONLY",
    )


# =============================================================================
# 9. STOCHASTIC PRICING ENGINE
# =============================================================================

def run_stochastic_pricing_fast(
    stochastic_catalogue: pd.DataFrame,
    sov: pd.DataFrame,
    policy_terms: PolicyTerms,
    year_col: str = "simulation_year",
    event_id_col: str = "event_id",
    peak_pfu_col: str = "peak_pfu",
    duration_col: str = "duration_hours",
    min_duration_hours: float = MIN_DURATION_HOURS_DEFAULT,
    pfu_trigger: float = PFU_TRIGGER_DEFAULT,
    min_route_exposure: float = MIN_ROUTE_EXPOSURE_DEFAULT,
    sample_size: int = None,
) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, float]]:
    """
    OPTIMIZED stochastic pricing engine with sampling and vectorization.
    
    Performance improvements:
    1. Optional sampling: Process only sample_size years (e.g. 10k instead of 100k)
    2. Pre-filtering: Remove non-triggering events upfront
    3. Vectorized tier assignment: Batch process tiers instead of row-by-row
    4. Early aggregate exhaustion: Skip years after aggregate is exhausted
    
    Args:
        sample_size: If provided, randomly sample this many years. None = use all years.
                    For bulk pricing, recommend 10000-20000 for 10-20x speedup.
    """
    required = {year_col, event_id_col, peak_pfu_col}
    missing = required - set(stochastic_catalogue.columns)
    if missing:
        raise KeyError(f"Missing stochastic catalogue columns: {sorted(missing)}")

    # Enrich SOV once (not per event)
    if 'route_exposure_source' not in sov.columns:
        sov_enriched = enrich_sov_route_exposure(sov)
    else:
        sov_enriched = sov
    
    # Pre-calculate flight exposure values (vectorized)
    flight_base_losses = (sov_enriched['reroute_cost_usd'] * sov_enriched['route_exposure']).values
    
    # Sample years if requested
    catalogue = stochastic_catalogue
    if sample_size is not None:
        unique_years = catalogue[year_col].unique()
        if len(unique_years) > sample_size:
            sampled_years = np.random.choice(unique_years, size=sample_size, replace=False)
            catalogue = catalogue[catalogue[year_col].isin(sampled_years)]
    
    # Pre-filter: Remove non-triggering events
    catalogue = catalogue[
        (catalogue[peak_pfu_col] >= pfu_trigger) & 
        (catalogue.get(duration_col, min_duration_hours) >= min_duration_hours)
    ].copy()
    
    if catalogue.empty:
        # No triggering events
        num_years = sample_size if sample_size else stochastic_catalogue[year_col].nunique()
        return (
            pd.DataFrame(),
            pd.DataFrame({'simulation_year': range(num_years), 'annual_loss': 0.0, 'oep_loss': 0.0,
                          'ground_up_loss': 0.0, 'ground_up_oep_loss': 0.0,
                          'aggregate_exhausted': False}),
            {'AAL': 0.0, 'SD': 0.0, 'CV': np.nan, 'TVaR_99': 0.0}
        )
    
    # Vectorized tier assignment
    catalogue['tier_multiplier'] = catalogue[peak_pfu_col].apply(tier_multiplier_pfu)
    
    event_records, annual_records = [], []

    for year, year_events in catalogue.groupby(year_col):
        remaining_aggregate = policy_terms.annual_aggregate
        annual_loss = 0.0
        annual_max_event_loss = 0.0
        ground_up_annual_loss = 0.0
        ground_up_max_event_loss = 0.0

        for _, event in year_events.iterrows():
            peak_pfu = float(event[peak_pfu_col])
            tier_mult = float(event['tier_multiplier'])
            
            # Vectorized flight loss calculation
            flight_losses = flight_base_losses * tier_mult
            gross_event_loss = float(flight_losses.sum())
            ground_up_annual_loss += gross_event_loss
            ground_up_max_event_loss = max(ground_up_max_event_loss, gross_event_loss)

            if remaining_aggregate <= 0:
                continue
            
            # Apply policy terms
            after_deductible = max(0.0, gross_event_loss - policy_terms.event_deductible)
            event_payout_before_aggregate = min(after_deductible, policy_terms.per_event_limit)
            final_payout = min(event_payout_before_aggregate, remaining_aggregate)
            
            remaining_aggregate = max(0.0, remaining_aggregate - final_payout)
            annual_loss += final_payout
            annual_max_event_loss = max(annual_max_event_loss, final_payout)

            event_records.append({
                "simulation_year": year,
                "event_id": event[event_id_col],
                "peak_pfu": peak_pfu,
                "duration_hours": event.get(duration_col, min_duration_hours),
                "severity_tier": get_pfu_tier(peak_pfu),
                "tier_multiplier": tier_mult,
                "gross_event_loss": gross_event_loss,
                "final_event_payout": final_payout,
                "remaining_aggregate_after_event": remaining_aggregate,
                "eligible_flight_count": len(sov_enriched),
            })

        annual_records.append({
            "simulation_year": year,
            "annual_loss": annual_loss,
            "oep_loss": annual_max_event_loss,
            "ground_up_loss": ground_up_annual_loss,
            "ground_up_oep_loss": ground_up_max_event_loss,
            "aggregate_exhausted": annual_loss >= policy_terms.annual_aggregate,
        })

    event_results = pd.DataFrame(event_records)
    annual_results = pd.DataFrame(annual_records)
    
    # Preserve zero-loss years so full-catalogue runs report the actual simulation period.
    target_years = unique_years if sample_size is None else (
        unique_years if len(unique_years) <= sample_size else sampled_years
    )
    if len(annual_results) < len(target_years):
        all_years = set(target_years)
        simulated_years = set(annual_results['simulation_year'].unique())
        missing_years = all_years - simulated_years
        
        if missing_years:
            zero_loss_records = pd.DataFrame([
                {'simulation_year': yr, 'annual_loss': 0.0, 'oep_loss': 0.0, 'aggregate_exhausted': False}
                for yr in missing_years
            ])
            annual_results = pd.concat([annual_results, zero_loss_records], ignore_index=True)
    
    return event_results, annual_results, calculate_pricing_metrics(annual_results, event_results)


def run_stochastic_pricing(
    stochastic_catalogue: pd.DataFrame,
    sov: pd.DataFrame,
    policy_terms: PolicyTerms,
    year_col: str = "simulation_year",
    event_id_col: str = "event_id",
    peak_pfu_col: str = "peak_pfu",
    duration_col: str = "duration_hours",
    min_duration_hours: float = MIN_DURATION_HOURS_DEFAULT,
    pfu_trigger: float = PFU_TRIGGER_DEFAULT,
    min_route_exposure: float = MIN_ROUTE_EXPOSURE_DEFAULT,
) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, float]]:
    """
    Original stochastic pricing engine (preserved for backward compatibility).
    For faster bulk pricing, use run_stochastic_pricing_fast() with sample_size parameter.
    """
    required = {year_col, event_id_col, peak_pfu_col}
    missing = required - set(stochastic_catalogue.columns)
    if missing:
        raise KeyError(f"Missing stochastic catalogue columns: {sorted(missing)}")

    if 'route_exposure_source' not in sov.columns:
        sov_enriched = enrich_sov_route_exposure(sov)
    else:
        sov_enriched = sov
    event_records, annual_records = [], []

    for year, year_events in stochastic_catalogue.groupby(year_col):
        remaining_aggregate = policy_terms.annual_aggregate
        annual_loss = 0.0
        annual_max_event_loss = 0.0
        ground_up_annual_loss = 0.0
        ground_up_max_event_loss = 0.0

        for _, event in year_events.iterrows():
            peak_pfu = float(event[peak_pfu_col])
            event_duration = float(event.get(duration_col, min_duration_hours))
            if peak_pfu < pfu_trigger or event_duration < min_duration_hours:
                continue

            event_policy = PolicyTerms(policy_terms.per_event_limit, policy_terms.event_deductible,
                                       policy_terms.annual_aggregate, remaining_aggregate)
            event_result = calculate_event_payout(sov_enriched, peak_pfu, event_policy, str(event[event_id_col]), min_route_exposure,
                                                  settlement_basis="STOCHASTIC_SCHEDULED_EXPOSED_FLIGHT")

            final_payout = event_result.final_event_payout
            ground_up_annual_loss += event_result.gross_event_loss
            ground_up_max_event_loss = max(ground_up_max_event_loss, event_result.gross_event_loss)
            if remaining_aggregate <= 0:
                continue
            remaining_aggregate = event_result.remaining_aggregate_after_event
            annual_loss += final_payout
            annual_max_event_loss = max(annual_max_event_loss, final_payout)

            event_records.append({
                "simulation_year": year,
                "event_id": event[event_id_col],
                "peak_pfu": peak_pfu,
                "duration_hours": event_duration,
                "severity_tier": event_result.severity_tier,
                "tier_multiplier": event_result.tier_multiplier,
                "gross_event_loss": event_result.gross_event_loss,
                "final_event_payout": final_payout,
                "remaining_aggregate_after_event": remaining_aggregate,
                "eligible_flight_count": event_result.eligible_flight_count,
            })

        annual_records.append({
            "simulation_year": year,
            "annual_loss": annual_loss,
            "oep_loss": annual_max_event_loss,
            "ground_up_loss": ground_up_annual_loss,
            "ground_up_oep_loss": ground_up_max_event_loss,
            "aggregate_exhausted": annual_loss >= policy_terms.annual_aggregate,
        })

    event_results = pd.DataFrame(event_records)
    annual_results = pd.DataFrame(annual_records)
    return event_results, annual_results, calculate_pricing_metrics(annual_results, event_results)


# =============================================================================
# 10. RISK METRICS AND PREMIUM
# =============================================================================

def return_period_loss(losses: Iterable[float], return_period: float) -> float:
    arr = np.asarray(list(losses), dtype=float)
    if arr.size == 0:
        return 0.0
    return float(np.quantile(arr, 1.0 - (1.0 / return_period)))


def tvar(losses: Iterable[float], quantile: float = 0.99) -> float:
    arr = np.asarray(list(losses), dtype=float)
    if arr.size == 0:
        return 0.0
    threshold = np.quantile(arr, quantile)
    tail = arr[arr >= threshold]
    return float(tail.mean()) if tail.size else float(threshold)


def calculate_pricing_metrics(
    annual_results: pd.DataFrame, 
    event_results: pd.DataFrame = None,
    annual_loss_col: str = "annual_loss", 
    oep_loss_col: str = "oep_loss"
) -> Dict[str, float]:
    """
    Calculate pricing metrics including tier-wise AAL contributions
    
    Args:
        annual_results: DataFrame with annual loss results
        event_results: Optional DataFrame with event-level results including severity_tier
        annual_loss_col: Column name for annual losses
        oep_loss_col: Column name for OEP losses
    
    Returns:
        Dict with AAL, SD, CV, return periods, TVaR, and tier-wise contributions
    """
    if annual_results.empty:
        return {
            "AAL": 0.0, "SD": 0.0, "CV": np.nan,
            "tier_1_aal": 0.0, "tier_1_pct": 0.0,
            "tier_2_aal": 0.0, "tier_2_pct": 0.0,
            "tier_3_aal": 0.0, "tier_3_pct": 0.0
        }

    losses = pd.to_numeric(annual_results[annual_loss_col], errors="coerce").fillna(0.0)
    oep_losses = pd.to_numeric(annual_results[oep_loss_col], errors="coerce").fillna(0.0)
    aal = float(losses.mean())
    sd = float(losses.std(ddof=1)) if len(losses) > 1 else 0.0
    cv = float(sd / aal) if aal > 0 else np.nan

    metrics = {
        "AAL": aal, "SD": sd, "CV": cv,
        "ZeroLossProbability": float((losses == 0).mean()),
        "AggregateExhaustionProbability": float(annual_results.get("aggregate_exhausted", pd.Series(False)).mean()),
        "AEP_1in10": return_period_loss(losses, 10),
        "AEP_1in25": return_period_loss(losses, 25),
        "AEP_1in50": return_period_loss(losses, 50),
        "AEP_1in100": return_period_loss(losses, 100),
        "AEP_1in200": return_period_loss(losses, 200),
        "OEP_1in10": return_period_loss(oep_losses, 10),
        "OEP_1in25": return_period_loss(oep_losses, 25),
        "OEP_1in50": return_period_loss(oep_losses, 50),
        "OEP_1in100": return_period_loss(oep_losses, 100),
        "OEP_1in200": return_period_loss(oep_losses, 200),
        "TVaR_99": tvar(losses, 0.99),
        "TVaR_995": tvar(losses, 0.995),
    }

    if 'ground_up_loss' in annual_results.columns:
        ground_up_losses = pd.to_numeric(annual_results['ground_up_loss'], errors='coerce').fillna(0.0)
        ground_up_oep_losses = pd.to_numeric(
            annual_results.get('ground_up_oep_loss', annual_results['ground_up_loss']),
            errors='coerce',
        ).fillna(0.0)
        metrics.update({
            "GroundUp_AAL": float(ground_up_losses.mean()),
            "GroundUp_AEP_1in100": return_period_loss(ground_up_losses, 100),
            "GroundUp_AEP_1in200": return_period_loss(ground_up_losses, 200),
            "GroundUp_OEP_1in100": return_period_loss(ground_up_oep_losses, 100),
            "GroundUp_OEP_1in200": return_period_loss(ground_up_oep_losses, 200),
            "GroundUp_MetricsAvailable": True,
        })
    else:
        metrics["GroundUp_MetricsAvailable"] = False
    
    # Calculate tier-wise AAL contributions
    if event_results is not None and not event_results.empty and 'severity_tier' in event_results.columns:
        num_years = annual_results['simulation_year'].nunique()
        
        # Calculate total payout by tier
        tier_1_total = event_results[event_results['severity_tier'] == 'TIER_1']['final_event_payout'].sum()
        tier_2_total = event_results[event_results['severity_tier'] == 'TIER_2']['final_event_payout'].sum()
        tier_3_total = event_results[event_results['severity_tier'] == 'TIER_3']['final_event_payout'].sum()
        
        # Calculate AAL by tier (total / num_years)
        tier_1_aal = float(tier_1_total / num_years) if num_years > 0 else 0.0
        tier_2_aal = float(tier_2_total / num_years) if num_years > 0 else 0.0
        tier_3_aal = float(tier_3_total / num_years) if num_years > 0 else 0.0
        
        # Calculate percentage contributions
        total_tier_aal = tier_1_aal + tier_2_aal + tier_3_aal
        tier_1_pct = float((tier_1_aal / total_tier_aal * 100)) if total_tier_aal > 0 else 0.0
        tier_2_pct = float((tier_2_aal / total_tier_aal * 100)) if total_tier_aal > 0 else 0.0
        tier_3_pct = float((tier_3_aal / total_tier_aal * 100)) if total_tier_aal > 0 else 0.0
        
        metrics.update({
            "tier_1_aal": tier_1_aal,
            "tier_1_pct": tier_1_pct,
            "tier_2_aal": tier_2_aal,
            "tier_2_pct": tier_2_pct,
            "tier_3_aal": tier_3_aal,
            "tier_3_pct": tier_3_pct,
        })
    else:
        # If no event results, set tier metrics to zero
        metrics.update({
            "tier_1_aal": 0.0,
            "tier_1_pct": 0.0,
            "tier_2_aal": 0.0,
            "tier_2_pct": 0.0,
            "tier_3_aal": 0.0,
            "tier_3_pct": 0.0,
        })
    
    return metrics


def gross_premium_simple(aal: float, load_factor: float = 0.40) -> float:
    return float(aal * (1.0 + load_factor))


def gross_premium_with_capital_load(aal: float, tvar_99: float, cost_of_capital: float = 0.08,
                                    expense_load: float = 0.15, profit_load: float = 0.10) -> Dict[str, float]:
    capital_at_risk = max(0.0, tvar_99 - aal)
    risk_load = capital_at_risk * cost_of_capital
    expense_amount = aal * expense_load
    profit_amount = aal * profit_load
    gross = aal + risk_load + expense_amount + profit_amount
    return {"AAL": aal, "CapitalAtRisk": capital_at_risk, "RiskLoad": risk_load,
            "ExpenseLoad": expense_amount, "ProfitLoad": profit_amount, "GrossPremium": gross}


# =============================================================================
# 11. EXPORT HELPERS
# =============================================================================

def event_result_to_dict(result: EventCalculationResult) -> Dict:
    out = asdict(result)
    out["flight_results"] = [asdict(x) for x in result.flight_results]
    return out


def event_result_to_dataframes(result: EventCalculationResult) -> Tuple[pd.DataFrame, pd.DataFrame]:
    summary = pd.DataFrame([{
        "event_id": result.event_id,
        "peak_pfu": result.peak_pfu,
        "severity_tier": result.severity_tier,
        "tier_multiplier": result.tier_multiplier,
        "gross_event_loss": result.gross_event_loss,
        "after_deductible": result.after_deductible,
        "event_payout_before_aggregate": result.event_payout_before_aggregate,
        "final_event_payout": result.final_event_payout,
        "remaining_aggregate_after_event": result.remaining_aggregate_after_event,
        "eligible_flight_count": result.eligible_flight_count,
    }])
    flight_audit = pd.DataFrame([asdict(x) for x in result.flight_results])
    return summary, flight_audit


# =============================================================================
# 12. DEMO / TEST EXAMPLES
# =============================================================================

def build_demo_sov() -> pd.DataFrame:
    # duration_hours, polar_hours and route_exposure can be blank. Engine derives them.
    data = [
        {"airline_id": "FIN", "flight_id": "AY105", "origin": "JFK", "destination": "HEL", "reroute_cost_usd": 18000,
         "duration_hours": np.nan, "polar_hours": np.nan, "route_exposure": np.nan},
        {"airline_id": "FIN", "flight_id": "AY107", "origin": "ORD", "destination": "HEL", "reroute_cost_usd": 22000,
         "duration_hours": 9.0, "polar_hours": np.nan, "route_exposure": np.nan},
        {"airline_id": "FIN", "flight_id": "AY091", "origin": "SFO", "destination": "HEL", "reroute_cost_usd": 35000,
         "duration_hours": 10.5, "polar_hours": 6.0, "route_exposure": np.nan},
    ]
    return enrich_sov_route_exposure(pd.DataFrame(data))


def build_demo_live_schedule() -> pd.DataFrame:
    return pd.DataFrame([
        {"airline_id": "FIN", "flight_id": "AY105", "origin": "JFK", "destination": "HEL", "scheduled_departure_utc": "2026-07-30T10:00:00Z", "flight_status": "ON_TIME"},
        {"airline_id": "FIN", "flight_id": "AY107", "origin": "ORD", "destination": "HEL", "scheduled_departure_utc": "2026-07-30T11:00:00Z", "flight_status": "DELAYED"},
        {"airline_id": "FIN", "flight_id": "AY091", "origin": "SFO", "destination": "HEL", "scheduled_departure_utc": "2026-07-30T12:00:00Z", "flight_status": "CANCELLED"},
    ])


def build_demo_live_pfu_readings() -> pd.DataFrame:
    return pd.DataFrame({
        "timestamp_utc": pd.date_range("2026-07-30T06:00:00Z", periods=7, freq="h"),
        "pfu": [1050, 1100, 1200, 1180, 1150, 1125, 1080],
    })


def build_demo_stochastic_catalogue() -> pd.DataFrame:
    return pd.DataFrame([
        {"simulation_year": 1, "event_id": "Y1_E1", "peak_pfu": 800, "duration_hours": 8},
        {"simulation_year": 1, "event_id": "Y1_E2", "peak_pfu": 5000, "duration_hours": 12},
        {"simulation_year": 2, "event_id": "Y2_E1", "peak_pfu": 10000, "duration_hours": 14},
        {"simulation_year": 3, "event_id": "Y3_E1", "peak_pfu": 25000, "duration_hours": 20},
        {"simulation_year": 4, "event_id": "Y4_E1", "peak_pfu": 1500, "duration_hours": 4},
        {"simulation_year": 5, "event_id": "Y5_E1", "peak_pfu": 50000, "duration_hours": 30},
    ])


def demo_live_claim() -> None:
    sov = build_demo_sov()
    scheduled_flights = build_demo_live_schedule()
    pfu_readings = build_demo_live_pfu_readings()
    policy = PolicyTerms(per_event_limit=500000, event_deductible=10000, annual_aggregate=15000000)

    print("\n=== SOV SCHEMA ===")
    print(get_sov_schema().to_string(index=False))
    print("\n=== ENRICHED SOV ===")
    print(sov.to_string(index=False))

    live_event = evaluate_live_pfu_event(pfu_readings, event_id="LIVE_20260730_001")
    result = calculate_live_claim_from_scheduled_flights(live_event, scheduled_flights, sov, policy)
    summary, audit = event_result_to_dataframes(result)
    print("\n=== LIVE EVENT ===")
    print(asdict(live_event))
    print("\n=== LIVE CLAIM SUMMARY ===")
    print(summary.to_string(index=False))
    print("\n=== LIVE FLIGHT AUDIT ===")
    print(audit.to_string(index=False))


def demo_stochastic_pricing() -> None:
    sov = build_demo_sov()
    stochastic_catalogue = build_demo_stochastic_catalogue()
    policy = PolicyTerms(per_event_limit=500000, event_deductible=10000, annual_aggregate=15000000)
    event_results, annual_results, metrics = run_stochastic_pricing(stochastic_catalogue, sov, policy)

    print("\n=== STOCHASTIC EVENT RESULTS ===")
    print(event_results.to_string(index=False))
    print("\n=== ANNUAL RESULTS ===")
    print(annual_results.to_string(index=False))
    print("\n=== PRICING METRICS ===")
    for k, v in metrics.items():
        print(f"{k}: {v}")

    premium = gross_premium_with_capital_load(metrics["AAL"], metrics["TVaR_99"])
    print("\n=== PREMIUM ===")
    for k, v in premium.items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    demo_live_claim()
    demo_stochastic_pricing()
