"""
Hilfslogik für Backup-/Stromausfall-Bewertungen der EcoFlow PowerOcean Integration.

Zweck:
    Dieses Modul kapselt alle abgeleiteten Berechnungen rund um Backup Helpers:
    - Normalisierte Leistungswerte
    - Nutzbare Energie oberhalb einer Reserve
    - Geglättete Last für Restlaufzeit-Schätzungen
    - Robuste Stromausfall-Heuristik

Input:
    - Coordinator-Daten (`dict[str, Any]`) aus MQTT / Protobuf-Dekodierung
    - Options-Flow-Werte für Backup Helpers
    - Kleine In-Memory-Historie der letzten Snapshots

Output:
    - Normalisierte Optionswerte
    - Einzelne Snapshots für Backup-Bewertungen
    - Zusammengefasste `BackupEvaluation`

Wichtige Invarianten:
    - Keine Netzwerkzugriffe und keine Home-Assistant-I/O in diesem Modul
    - Bei unsicheren oder unplausiblen Daten lieber `None` als Fantasiewerte
    - Stromausfall-Erkennung soll konservativ sein und Fehlalarme minimieren

Debug-Hinweis:
    - Dieses Modul liefert erklärende Statusfelder wie `outage_reason`,
      `smoothed_load_power_w` und `runtime_estimate_minutes`.
    - Diese Werte können über Entity-Attribute oder Diagnostics geprüft werden.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Mapping, Sequence

from .const import (
    BACKUP_CRITICAL_RUNTIME_MINUTES_MAX,
    BACKUP_CRITICAL_RUNTIME_MINUTES_MIN,
    BACKUP_RESERVED_SOC_PERCENT_MAX,
    BACKUP_RESERVED_SOC_PERCENT_MIN,
    CONF_BACKUP_CRITICAL_RUNTIME_MINUTES,
    CONF_BACKUP_RESERVED_SOC_PERCENT,
    CONF_BACKUP_RUNTIME_SMOOTHING_MINUTES,
    CONF_ENABLE_BACKUP_HELPERS,
    CONF_POWER_OUTAGE_FREQUENCY_MIN_HZ,
    CONF_POWER_OUTAGE_GRID_POWER_THRESHOLD_W,
    DATA_BATTERIES,
    DATA_EMS_HEARTBEAT,
    DATA_ENERGY_STREAM,
    DEFAULT_BACKUP_CRITICAL_RUNTIME_MINUTES,
    DEFAULT_BACKUP_RESERVED_SOC_PERCENT,
    DEFAULT_BACKUP_RUNTIME_SMOOTHING_MINUTES,
    DEFAULT_ENABLE_BACKUP_HELPERS,
    DEFAULT_POWER_OUTAGE_FREQUENCY_MIN_HZ,
    DEFAULT_POWER_OUTAGE_GRID_POWER_THRESHOLD_W,
    POWER_OUTAGE_FREQUENCY_MIN_HZ_MAX,
    POWER_OUTAGE_FREQUENCY_MIN_HZ_MIN,
    POWER_OUTAGE_GRID_POWER_THRESHOLD_W_MAX,
    POWER_OUTAGE_GRID_POWER_THRESHOLD_W_MIN,
    BACKUP_RUNTIME_SMOOTHING_MINUTES_MAX,
    BACKUP_RUNTIME_SMOOTHING_MINUTES_MIN,
)

# Kleine Netzleistungen liegen häufig nur im Messrauschen.
GRID_SIGN_DEADBAND_W = 20.0
# Ein Vorzeichenwechsel wird nur akzeptiert, wenn die Bilanz spürbar besser wird.
MIN_SIGN_FLIP_IMPROVEMENT_W = 20.0

# Outage-Erkennung bewusst konservativ:
# - Hauslast muss spürbar vorhanden sein
# - Lokale Versorgung muss sichtbar sein
# - mehrere Samples über ein kurzes Zeitfenster müssen dieselben Bedingungen zeigen
MIN_BACKUP_LOAD_W = 100.0
MIN_LOCAL_SUPPLY_W = 100.0
OUTAGE_CONFIRMATION_SECONDS = 45
MIN_OUTAGE_SAMPLES = 2
BACKUP_HISTORY_MINUTES = 20

BACKUP_ACTION_NORMAL = "normal"
BACKUP_ACTION_SHED_LOAD = "shed_load"
BACKUP_ACTION_SHUTDOWN_RECOMMENDED = "shutdown_recommended"
BACKUP_ACTION_UNKNOWN = "unknown"
BACKUP_RECOMMENDED_ACTION_OPTIONS = [
    BACKUP_ACTION_NORMAL,
    BACKUP_ACTION_SHED_LOAD,
    BACKUP_ACTION_SHUTDOWN_RECOMMENDED,
    BACKUP_ACTION_UNKNOWN,
]


@dataclass(frozen=True, slots=True)
class BackupHelperConfig:
    """Normalisierte Backup-Helper-Konfiguration."""

    enabled: bool
    reserved_soc_percent: int
    grid_power_threshold_w: int
    frequency_min_hz: float
    runtime_smoothing_minutes: int
    critical_runtime_minutes: int


@dataclass(frozen=True, slots=True)
class BackupSnapshot:
    """
    Einzelner Messpunkt für Backup-Bewertungen.

    Beispiel:
        Input:
            soc=55, energy_wh=10800, load=780, solar=0, battery=750, grid=5, freq=None
        Output:
            Snapshot mit denselben normalisierten Kernwerten und UTC-Zeitstempel
    """

    observed_at: datetime
    total_soc_percent: int | None
    total_energy_wh: float | None
    load_power_w: float | None
    grid_power_w: float | None
    solar_power_w: float | None
    battery_power_w: float | None
    grid_frequency_hz: float | None


@dataclass(frozen=True, slots=True)
class BackupEvaluation:
    """Abgeleitete Backup-Bewertung aus letzter Datenlage und kurzer Historie."""

    enabled: bool
    observed_at: datetime | None
    usable_energy_wh: float | None
    smoothed_load_power_w: float | None
    runtime_estimate_minutes: float | None
    runtime_estimate_hours: float | None
    backup_reserve_critical: bool
    power_outage: bool
    backup_active: bool
    recommended_action: str
    outage_reason: str
    has_seen_valid_grid_frequency: bool

    def as_dict(self) -> dict[str, Any]:
        """Hilfsdarstellung für Diagnostics und Debug-Attribute."""
        return asdict(self)


def _clamp(value: float, minimum: float, maximum: float) -> float:
    """Begrenzt einen numerischen Wert sicher auf einen gültigen Bereich."""
    return max(minimum, min(maximum, value))


def _coerce_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    """Konvertiert Eingaben robust zu int und wendet Grenzen an."""
    try:
        coerced = int(float(value))
    except (TypeError, ValueError):
        coerced = default
    return int(_clamp(coerced, minimum, maximum))


def _coerce_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    """Konvertiert Eingaben robust zu float und wendet Grenzen an."""
    try:
        coerced = float(value)
    except (TypeError, ValueError):
        coerced = default
    return float(_clamp(coerced, minimum, maximum))


def normalize_backup_helper_options(raw_options: Mapping[str, Any]) -> dict[str, Any]:
    """
    Normalisiert alle Backup-Helper-Optionen in einer zentralen Stelle.

    Warum:
        Sowohl Options Flow als auch Coordinator sollen mit denselben Regeln
        arbeiten. So vermeiden wir doppelte Validierungslogik.
    """

    return {
        CONF_ENABLE_BACKUP_HELPERS: bool(
            raw_options.get(CONF_ENABLE_BACKUP_HELPERS, DEFAULT_ENABLE_BACKUP_HELPERS)
        ),
        CONF_BACKUP_RESERVED_SOC_PERCENT: _coerce_int(
            raw_options.get(
                CONF_BACKUP_RESERVED_SOC_PERCENT,
                DEFAULT_BACKUP_RESERVED_SOC_PERCENT,
            ),
            DEFAULT_BACKUP_RESERVED_SOC_PERCENT,
            BACKUP_RESERVED_SOC_PERCENT_MIN,
            BACKUP_RESERVED_SOC_PERCENT_MAX,
        ),
        CONF_POWER_OUTAGE_GRID_POWER_THRESHOLD_W: _coerce_int(
            raw_options.get(
                CONF_POWER_OUTAGE_GRID_POWER_THRESHOLD_W,
                DEFAULT_POWER_OUTAGE_GRID_POWER_THRESHOLD_W,
            ),
            DEFAULT_POWER_OUTAGE_GRID_POWER_THRESHOLD_W,
            POWER_OUTAGE_GRID_POWER_THRESHOLD_W_MIN,
            POWER_OUTAGE_GRID_POWER_THRESHOLD_W_MAX,
        ),
        CONF_POWER_OUTAGE_FREQUENCY_MIN_HZ: _coerce_float(
            raw_options.get(
                CONF_POWER_OUTAGE_FREQUENCY_MIN_HZ,
                DEFAULT_POWER_OUTAGE_FREQUENCY_MIN_HZ,
            ),
            DEFAULT_POWER_OUTAGE_FREQUENCY_MIN_HZ,
            POWER_OUTAGE_FREQUENCY_MIN_HZ_MIN,
            POWER_OUTAGE_FREQUENCY_MIN_HZ_MAX,
        ),
        CONF_BACKUP_RUNTIME_SMOOTHING_MINUTES: _coerce_int(
            raw_options.get(
                CONF_BACKUP_RUNTIME_SMOOTHING_MINUTES,
                DEFAULT_BACKUP_RUNTIME_SMOOTHING_MINUTES,
            ),
            DEFAULT_BACKUP_RUNTIME_SMOOTHING_MINUTES,
            BACKUP_RUNTIME_SMOOTHING_MINUTES_MIN,
            BACKUP_RUNTIME_SMOOTHING_MINUTES_MAX,
        ),
        CONF_BACKUP_CRITICAL_RUNTIME_MINUTES: _coerce_int(
            raw_options.get(
                CONF_BACKUP_CRITICAL_RUNTIME_MINUTES,
                DEFAULT_BACKUP_CRITICAL_RUNTIME_MINUTES,
            ),
            DEFAULT_BACKUP_CRITICAL_RUNTIME_MINUTES,
            BACKUP_CRITICAL_RUNTIME_MINUTES_MIN,
            BACKUP_CRITICAL_RUNTIME_MINUTES_MAX,
        ),
    }


def backup_helper_config_from_mapping(raw_options: Mapping[str, Any]) -> BackupHelperConfig:
    """Erzeugt aus beliebigen Optionen eine typsichere Backup-Konfiguration."""
    normalized = normalize_backup_helper_options(raw_options)
    return BackupHelperConfig(
        enabled=bool(normalized[CONF_ENABLE_BACKUP_HELPERS]),
        reserved_soc_percent=int(normalized[CONF_BACKUP_RESERVED_SOC_PERCENT]),
        grid_power_threshold_w=int(
            normalized[CONF_POWER_OUTAGE_GRID_POWER_THRESHOLD_W]
        ),
        frequency_min_hz=float(normalized[CONF_POWER_OUTAGE_FREQUENCY_MIN_HZ]),
        runtime_smoothing_minutes=int(
            normalized[CONF_BACKUP_RUNTIME_SMOOTHING_MINUTES]
        ),
        critical_runtime_minutes=int(
            normalized[CONF_BACKUP_CRITICAL_RUNTIME_MINUTES]
        ),
    )


def backup_history_retention_minutes(config: BackupHelperConfig) -> int:
    """Länge der In-Memory-Historie für Glättung und Outage-Stabilität."""
    return max(BACKUP_HISTORY_MINUTES, config.runtime_smoothing_minutes + 5)


def _ems_grid_power_w(data: Mapping[str, Any]) -> float:
    """Fallback: Netzleistung aus den drei Phasen (EMS_HEARTBEAT)."""
    ems = data.get(DATA_EMS_HEARTBEAT)
    if ems is None:
        return 0.0
    return float(ems.phase_a.act_pwr + ems.phase_b.act_pwr + ems.phase_c.act_pwr)


def _ems_solar_power_w(data: Mapping[str, Any]) -> float:
    """Fallback: Solarleistung als Summe aller MPPT-Strings."""
    ems = data.get(DATA_EMS_HEARTBEAT)
    if ems is None:
        return 0.0
    return float(sum(string.power_w for string in ems.mppt_strings))


def _ems_battery_power_w(data: Mapping[str, Any]) -> float:
    """Fallback: Batterieleistung aus EMS_HEARTBEAT."""
    ems = data.get(DATA_EMS_HEARTBEAT)
    if ems is None:
        return 0.0
    return float(ems.battery_power_w)


def normalized_power_components(data: Mapping[str, Any]) -> tuple[float, float, float, float]:
    """
    Liefert normalisierte Leistungswerte als `(solar, grid, load, battery)`.

    Warum:
        Einige Firmwarestände liefern wechselnde Vorzeichenkonventionen.
        Die physikalische Bilanz `load ~= solar + battery + grid` hilft,
        die Rohwerte robust in ein konsistentes Modell zu überführen.
    """

    stream = data.get(DATA_ENERGY_STREAM)
    if stream is None:
        solar = _ems_solar_power_w(data)
        grid = _ems_grid_power_w(data)
        battery = _ems_battery_power_w(data)
        load = solar + battery + grid
        return solar, grid, load, battery

    solar = float(stream.solar_w)
    load = float(stream.load_w)
    grid_raw = float(stream.grid_w)
    battery_raw = float(stream.battery_w)

    if abs(grid_raw) <= GRID_SIGN_DEADBAND_W:
        grid = grid_raw
    else:
        err_grid_keep = abs((solar + battery_raw + grid_raw) - load)
        err_grid_flip = abs((solar + battery_raw - grid_raw) - load)
        grid = -grid_raw if (err_grid_keep - err_grid_flip) >= MIN_SIGN_FLIP_IMPROVEMENT_W and err_grid_flip < err_grid_keep else grid_raw

    battery_expected = load - solar - grid
    err_batt_keep = abs(battery_raw - battery_expected)
    err_batt_flip = abs((-battery_raw) - battery_expected)
    battery = -battery_raw if (err_batt_keep - err_batt_flip) >= MIN_SIGN_FLIP_IMPROVEMENT_W and err_batt_flip < err_batt_keep else battery_raw

    return solar, grid, load, battery


def solar_power_w(data: Mapping[str, Any]) -> float:
    """Solarleistung in Watt."""
    return normalized_power_components(data)[0]


def grid_power_w(data: Mapping[str, Any]) -> float:
    """Netzleistung in Watt, positiv für Bezug und negativ für Einspeisung."""
    return normalized_power_components(data)[1]


def load_power_w(data: Mapping[str, Any]) -> float:
    """Hausverbrauch in Watt."""
    return normalized_power_components(data)[2]


def battery_power_w(data: Mapping[str, Any]) -> float:
    """Batterieleistung in Watt, positiv für Entladen und negativ für Laden."""
    return normalized_power_components(data)[3]


def total_soc_percent(data: Mapping[str, Any]) -> int | None:
    """Gesamt-SOC bevorzugt aus ENERGY_STREAM, sonst Mittelwert aller Packs."""
    stream = data.get(DATA_ENERGY_STREAM)
    if stream is not None:
        try:
            stream_soc = int(getattr(stream, "soc", 0))
        except (TypeError, ValueError):
            stream_soc = 0
        if 0 <= stream_soc <= 100:
            return stream_soc

    batteries = data.get(DATA_BATTERIES, {})
    if not batteries:
        return None
    return int(sum(pack.soc for pack in batteries.values()) / len(batteries))


def total_energy_wh(data: Mapping[str, Any]) -> float | None:
    """
    Liefert die aktuell verfügbare Batterie-Gesamtenergie in Wh.

    Bevorzugt wird der Systemwert aus `EMS_HEARTBEAT.bp_remain_wh`.
    Falls dieser fehlt, wird die Summe der Einzelpacks verwendet.
    """

    ems = data.get(DATA_EMS_HEARTBEAT)
    if ems is not None:
        try:
            bp_remain_wh = float(getattr(ems, "bp_remain_wh", 0.0))
        except (TypeError, ValueError):
            bp_remain_wh = 0.0
        if bp_remain_wh > 0:
            return bp_remain_wh

    batteries = data.get(DATA_BATTERIES, {})
    if not batteries:
        return None

    remaining_values = [
        float(pack.remaining_wh)
        for pack in batteries.values()
        if getattr(pack, "remaining_wh", 0) > 0
    ]
    if not remaining_values:
        return None
    return sum(remaining_values)


def grid_frequency_hz(data: Mapping[str, Any]) -> float | None:
    """Netzfrequenz in Hz oder `None`, wenn kein brauchbarer Wert vorhanden ist."""
    ems = data.get(DATA_EMS_HEARTBEAT)
    if ems is None:
        return None

    try:
        frequency = float(getattr(ems, "frequency_hz", 0.0))
    except (TypeError, ValueError):
        return None
    return frequency if frequency >= 0.1 else None


def build_backup_snapshot(
    data: Mapping[str, Any],
    observed_at: datetime | None = None,
) -> BackupSnapshot:
    """Erzeugt einen normalisierten Snapshot aus den aktuellen Coordinator-Daten."""
    timestamp = observed_at or datetime.now(UTC)
    return BackupSnapshot(
        observed_at=timestamp,
        total_soc_percent=total_soc_percent(data),
        total_energy_wh=total_energy_wh(data),
        load_power_w=load_power_w(data),
        grid_power_w=grid_power_w(data),
        solar_power_w=solar_power_w(data),
        battery_power_w=battery_power_w(data),
        grid_frequency_hz=grid_frequency_hz(data),
    )


def trim_backup_history(
    snapshots: Sequence[BackupSnapshot],
    *,
    now: datetime,
    retention_minutes: int,
) -> list[BackupSnapshot]:
    """Schneidet eine Snapshot-Historie zeitbasiert zu."""
    cutoff = now - timedelta(minutes=retention_minutes)
    return [snapshot for snapshot in snapshots if snapshot.observed_at >= cutoff]


def calculate_backup_usable_energy_wh(
    total_energy_wh_value: float | None,
    soc_percent: int | None,
    reserved_soc_percent: int,
) -> float | None:
    """
    Berechnet die oberhalb der Reserve nutzbare Energie.

    Formel laut Produktentscheidung:
        usable_energy_wh = max(total_energy_wh * ((soc - reserve) / 100), 0)
    """

    if total_energy_wh_value is None or soc_percent is None:
        return None
    if total_energy_wh_value <= 0:
        return None
    if not 0 <= soc_percent <= 100:
        return None

    usable_energy_wh = total_energy_wh_value * (
        (soc_percent - reserved_soc_percent) / 100.0
    )
    return round(max(usable_energy_wh, 0.0), 1)


def smoothed_load_power_w(
    snapshots: Sequence[BackupSnapshot],
    *,
    now: datetime,
    smoothing_minutes: int,
) -> float | None:
    """Glättet den Hausverbrauch über ein Zeitfenster, um Peak-Rauschen zu entschärfen."""
    window_start = now - timedelta(minutes=smoothing_minutes)
    loads = [
        float(snapshot.load_power_w)
        for snapshot in snapshots
        if snapshot.observed_at >= window_start
        and snapshot.load_power_w is not None
        and snapshot.load_power_w > 0
    ]
    if not loads:
        return None
    return round(sum(loads) / len(loads), 1)


def runtime_estimate_minutes(
    *,
    usable_energy_wh_value: float | None,
    smoothed_load_power_w_value: float | None,
    latest_load_power_w_value: float | None,
) -> float | None:
    """Schätzt die Restlaufzeit in Minuten oder `None`, wenn keine sinnvolle Schätzung möglich ist."""
    if usable_energy_wh_value is None or smoothed_load_power_w_value is None:
        return None
    if usable_energy_wh_value <= 0:
        return 0.0
    if latest_load_power_w_value is None or latest_load_power_w_value <= 0:
        return None
    if smoothed_load_power_w_value <= 0:
        return None
    return round((usable_energy_wh_value / smoothed_load_power_w_value) * 60.0, 1)


def _sample_indicates_local_supply(
    snapshot: BackupSnapshot,
    *,
    min_local_supply_w: float,
) -> bool:
    """Prüft, ob PV/Batterie lokal genug Leistung bereitstellen, um Inselbetrieb zu stützen."""
    solar = max(snapshot.solar_power_w or 0.0, 0.0)
    battery_discharge = max(snapshot.battery_power_w or 0.0, 0.0)
    return (solar + battery_discharge) >= min_local_supply_w


def _sample_indicates_house_load(
    snapshot: BackupSnapshot,
    *,
    min_backup_load_w: float,
) -> bool:
    """Prüft, ob am Haus eine echte Last anliegt und nicht nur Leerlaufrauschen."""
    return (snapshot.load_power_w or 0.0) >= min_backup_load_w


def _sample_indicates_grid_quiet(
    snapshot: BackupSnapshot,
    *,
    grid_power_threshold_w: int,
) -> bool:
    """Prüft, ob die Netzleistung keine normale Versorgung erkennen lässt."""
    return abs(snapshot.grid_power_w or 0.0) <= float(grid_power_threshold_w)


def _recent_outage_samples(
    snapshots: Sequence[BackupSnapshot],
    *,
    now: datetime,
) -> list[BackupSnapshot]:
    """Liefert nur den kurzen, stabilen Zeitraum für die Outage-Heuristik."""
    window_start = now - timedelta(seconds=OUTAGE_CONFIRMATION_SECONDS)
    return [snapshot for snapshot in snapshots if snapshot.observed_at >= window_start]


def evaluate_backup_state(
    snapshots: Sequence[BackupSnapshot],
    *,
    config: BackupHelperConfig,
    has_seen_valid_grid_frequency: bool,
) -> BackupEvaluation:
    """
    Leitet aus Snapshot-Historie eine konservative Backup-/Outage-Bewertung ab.

    Stromausfall-Heuristik:
        Ein Ausfall wird erst dann als "wahrscheinlich" gewertet, wenn über einen
        kurzen stabilen Zeitraum gleichzeitig gilt:
        - Frequenzsignal war grundsätzlich schon einmal brauchbar und ist jetzt
          fehlend/unter Mindestfrequenz
        - Netzleistung bleibt nahe null
        - Hauslast ist vorhanden
        - PV/Batterie versorgen das Haus plausibel lokal weiter

    Diese Heuristik vermeidet absichtlich Fehlalarme im normalen Nullpunktbetrieb.
    """

    latest = snapshots[-1] if snapshots else None
    if not config.enabled or latest is None:
        return BackupEvaluation(
            enabled=False,
            observed_at=latest.observed_at if latest is not None else None,
            usable_energy_wh=None,
            smoothed_load_power_w=None,
            runtime_estimate_minutes=None,
            runtime_estimate_hours=None,
            backup_reserve_critical=False,
            power_outage=False,
            backup_active=False,
            recommended_action=BACKUP_ACTION_UNKNOWN,
            outage_reason="backup_helpers_disabled",
            has_seen_valid_grid_frequency=has_seen_valid_grid_frequency,
        )

    now = latest.observed_at
    smoothed_load = smoothed_load_power_w(
        snapshots,
        now=now,
        smoothing_minutes=config.runtime_smoothing_minutes,
    )
    usable_energy = calculate_backup_usable_energy_wh(
        latest.total_energy_wh,
        latest.total_soc_percent,
        config.reserved_soc_percent,
    )
    runtime_minutes_value = runtime_estimate_minutes(
        usable_energy_wh_value=usable_energy,
        smoothed_load_power_w_value=smoothed_load,
        latest_load_power_w_value=latest.load_power_w,
    )
    runtime_hours_value = (
        round(runtime_minutes_value / 60.0, 2)
        if runtime_minutes_value is not None
        else None
    )
    backup_reserve_critical = (
        runtime_minutes_value is not None
        and runtime_minutes_value <= config.critical_runtime_minutes
    )

    recent_samples = _recent_outage_samples(snapshots, now=now)
    min_load = max(MIN_BACKUP_LOAD_W, float(config.grid_power_threshold_w) * 2.0)
    min_local_supply = max(
        MIN_LOCAL_SUPPLY_W,
        float(config.grid_power_threshold_w) * 2.0,
    )

    if len(recent_samples) < MIN_OUTAGE_SAMPLES:
        power_outage = False
        outage_reason = "awaiting_stable_samples"
    elif not has_seen_valid_grid_frequency:
        power_outage = False
        outage_reason = "grid_frequency_signal_unavailable"
    elif not all(
        (sample.grid_frequency_hz is None or sample.grid_frequency_hz < config.frequency_min_hz)
        for sample in recent_samples
    ):
        power_outage = False
        outage_reason = "grid_frequency_still_present"
    elif not all(
        _sample_indicates_grid_quiet(
            sample,
            grid_power_threshold_w=config.grid_power_threshold_w,
        )
        for sample in recent_samples
    ):
        power_outage = False
        outage_reason = "grid_power_above_threshold"
    elif not all(
        _sample_indicates_house_load(sample, min_backup_load_w=min_load)
        for sample in recent_samples
    ):
        power_outage = False
        outage_reason = "house_load_too_low"
    elif not all(
        _sample_indicates_local_supply(
            sample,
            min_local_supply_w=min_local_supply,
        )
        for sample in recent_samples
    ):
        power_outage = False
        outage_reason = "local_supply_not_detected"
    else:
        power_outage = True
        outage_reason = "grid_outage_likely"

    backup_active = power_outage and _sample_indicates_local_supply(
        latest,
        min_local_supply_w=min_local_supply,
    )

    if runtime_minutes_value is None:
        recommended_action = BACKUP_ACTION_UNKNOWN
    elif runtime_minutes_value <= max(15, config.critical_runtime_minutes / 2):
        recommended_action = BACKUP_ACTION_SHUTDOWN_RECOMMENDED
    elif runtime_minutes_value <= config.critical_runtime_minutes:
        recommended_action = BACKUP_ACTION_SHED_LOAD
    else:
        recommended_action = BACKUP_ACTION_NORMAL

    return BackupEvaluation(
        enabled=True,
        observed_at=latest.observed_at,
        usable_energy_wh=usable_energy,
        smoothed_load_power_w=smoothed_load,
        runtime_estimate_minutes=runtime_minutes_value,
        runtime_estimate_hours=runtime_hours_value,
        backup_reserve_critical=backup_reserve_critical,
        power_outage=power_outage,
        backup_active=backup_active,
        recommended_action=recommended_action,
        outage_reason=outage_reason,
        has_seen_valid_grid_frequency=has_seen_valid_grid_frequency,
    )
