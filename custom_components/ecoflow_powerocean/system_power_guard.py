"""
System-Power-Reserve-Automatik für EcoFlow PowerOcean.

Zweck:
    Dieses Modul schaltet den optionalen System-Power-Befehl kontrolliert, wenn
    der Gesamt-SOC nachts die konfigurierte Backup-Reserve erreicht.

Input:
    - `coordinator.data` mit aktuellem Gesamt-SOC
    - `coordinator.system_power_on` als bester bekannter System-Power-Zustand
    - Options-Flow-Werte für Aktivierung, Reserve und Einschalt-Hysterese
    - `sun.sun` für Tag-/Nachtstatus

Output:
    - Optionaler MQTT-Befehl: System ausschalten oder wieder einschalten
    - Persistenter Merker in `.storage`, ob die Integration selbst abgeschaltet hat

Wichtige Invarianten:
    - Das Feature ist standardmäßig deaktiviert.
    - Die Integration schaltet nur wieder ein, wenn sie zuvor selbst
      ausgeschaltet hat.
    - Bei erkanntem Stromausfall oder aktivem Backupbetrieb wird nicht geschaltet.
    - Wenn `sun.sun` fehlt oder unklar ist, wird nicht ausgeschaltet. Dadurch
      bleibt die Anlage lieber an, statt nachts versehentlich auszugehen.

Debug-Hinweis:
    - Bei unerwartetem Verhalten Debug-Modus aktivieren und nach
      `Reserve-Automatik` im Home-Assistant-Log suchen.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import logging
from typing import Any, Callable, Mapping, TYPE_CHECKING

from .backup_helpers import normalize_backup_helper_options, total_soc_percent
from .const import (
    CONF_BACKUP_RESERVED_SOC_PERCENT,
    CONF_ENABLE_SYSTEM_POWER_RESERVE_GUARD,
    CONF_SYSTEM_POWER_RESERVE_RESTART_MARGIN_PERCENT,
    DEFAULT_ENABLE_SYSTEM_POWER_RESERVE_GUARD,
    DEFAULT_SYSTEM_POWER_RESERVE_RESTART_MARGIN_PERCENT,
    DOMAIN,
    SYSTEM_POWER_RESERVE_RESTART_MARGIN_MAX,
    SYSTEM_POWER_RESERVE_RESTART_MARGIN_MIN,
)
from .daily_report import schedule_home_assistant_task

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .coordinator import EcoFlowCoordinator

_LOGGER = logging.getLogger(__name__)

SYSTEM_POWER_RESERVE_GUARD_DATA_KEY = f"{DOMAIN}_system_power_reserve_guard"
STORAGE_VERSION = 1
STORAGE_KEY_PREFIX = f"{DOMAIN}.system_power_reserve_guard"
COMMAND_THROTTLE_SECONDS = 5 * 60

ACTION_TURN_OFF = "turn_off"
ACTION_TURN_ON = "turn_on"


@dataclass(frozen=True, slots=True)
class SystemPowerReserveGuardDecision:
    """
    Ergebnis der reinen Reserve-Entscheidung.

    Beispiel:
        Input: Nacht, SOC 10 %, Reserve 10 %, System an
        Output: action="turn_off", reason="reserve_reached"
    """

    action: str | None
    reason: str
    clear_auto_powered_off: bool = False


@dataclass(slots=True)
class SystemPowerReserveGuardState:
    """Persistenter Zustand der Reserve-Automatik."""

    auto_powered_off: bool = False
    last_action: str | None = None
    last_action_iso: str | None = None
    last_reason: str | None = None

    @classmethod
    def from_mapping(
        cls,
        raw: Mapping[str, Any] | None,
    ) -> "SystemPowerReserveGuardState":
        """Lädt den Storage-Zustand defensiv aus beliebigen Daten."""
        if not isinstance(raw, Mapping):
            return cls()
        return cls(
            auto_powered_off=bool(raw.get("auto_powered_off", False)),
            last_action=_coerce_optional_str(raw.get("last_action")),
            last_action_iso=_coerce_optional_str(raw.get("last_action_iso")),
            last_reason=_coerce_optional_str(raw.get("last_reason")),
        )

    def as_dict(self) -> dict[str, Any]:
        """JSON-kompatible Darstellung für Home-Assistant-Storage."""
        return asdict(self)


class SystemPowerReserveGuardManager:
    """
    Home-Assistant-Anbindung für die optionale Reserve-Automatik.

    Warum eigener Manager:
        Der System-Power-Schalter bleibt eine manuelle Entität. Automatische
        Schaltlogik ist ein Seiteneffekt und wird separat persistiert, damit
        manuelle Nutzeraktionen nicht übersteuert werden.
    """

    def __init__(
        self,
        hass: "HomeAssistant",
        entry: "ConfigEntry",
        coordinator: "EcoFlowCoordinator",
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self.options = normalize_system_power_reserve_guard_options(entry.options)
        self.backup_options = normalize_backup_helper_options(entry.options)
        self.state = SystemPowerReserveGuardState()
        self._store: Any = None
        self._last_command_at: datetime | None = None

    async def async_setup(self) -> None:
        """Lädt Zustand, registriert Listener und prüft den aktuellen Stand."""
        from homeassistant.helpers.storage import Store

        self._store = Store(
            self.hass,
            STORAGE_VERSION,
            f"{STORAGE_KEY_PREFIX}.{self.entry.entry_id}",
        )
        self.state = SystemPowerReserveGuardState.from_mapping(
            await self._store.async_load()
        )

        remove_update_listener = self.coordinator.async_add_listener(
            self._schedule_update_from_coordinator
        )
        self.entry.async_on_unload(remove_update_listener)

        remove_sunrise_listener = self._async_track_sunrise()
        if remove_sunrise_listener is not None:
            self.entry.async_on_unload(remove_sunrise_listener)

        await self.async_process_coordinator_update(force_save=True)

    async def async_shutdown(self) -> None:
        """Erzwingt einen letzten Storage-Schreibvorgang beim Entladen."""
        await self._async_save_state(force=True)

    def _schedule_update_from_coordinator(self) -> None:
        """Startet die async Verarbeitung aus dem Coordinator-Listener heraus."""
        schedule_home_assistant_task(
            self.hass,
            self.async_process_coordinator_update(),
        )

    async def async_process_coordinator_update(
        self,
        *,
        force_day: bool = False,
        force_save: bool = False,
    ) -> None:
        """Wertet SOC, Sonne und Systemstatus aus und schaltet bei Bedarf."""
        if not bool(self.options[CONF_ENABLE_SYSTEM_POWER_RESERVE_GUARD]):
            return

        data = getattr(self.coordinator, "data", None)
        soc_percent = _safe_total_soc_percent(data)
        reserve_soc_percent = int(self.backup_options[CONF_BACKUP_RESERVED_SOC_PERCENT])
        restart_margin_percent = int(
            self.options[CONF_SYSTEM_POWER_RESERVE_RESTART_MARGIN_PERCENT]
        )
        power_outage, backup_active = self._backup_outage_state()

        decision = decide_system_power_reserve_guard(
            now_is_day=force_day or self._daylight_or_unknown(),
            soc_percent=soc_percent,
            reserve_soc_percent=reserve_soc_percent,
            restart_margin_percent=restart_margin_percent,
            system_power_on=self.coordinator.system_power_on,
            auto_powered_off=self.state.auto_powered_off,
            power_outage=power_outage,
            backup_active=backup_active,
        )

        if decision.clear_auto_powered_off:
            self.state.auto_powered_off = False
            self.state.last_action = None
            self.state.last_reason = decision.reason
            self.state.last_action_iso = self._local_now().isoformat()
            await self._async_save_state(force=True)
            _LOGGER.info(
                "Reserve-Automatik: automatischer Aus-Merker gelöscht (%s)",
                decision.reason,
            )
            return

        if decision.action is None:
            await self._async_save_state(force=force_save)
            return

        if self._command_recently_sent():
            _LOGGER.debug(
                "Reserve-Automatik: Schaltbefehl wegen kurzer Wiederholung "
                "übersprungen (%s)",
                decision.reason,
            )
            return

        turn_on = decision.action == ACTION_TURN_ON
        try:
            await self.coordinator.async_set_system_power(turn_on)
        except Exception as exc:
            _LOGGER.warning(
                "Reserve-Automatik konnte System-Power nicht auf %s setzen: %s",
                "AN" if turn_on else "AUS",
                exc,
            )
            return

        self._last_command_at = self._local_now()
        self.state.auto_powered_off = not turn_on
        self.state.last_action = decision.action
        self.state.last_reason = decision.reason
        self.state.last_action_iso = self._last_command_at.isoformat()
        await self._async_save_state(force=True)
        _LOGGER.info(
            "Reserve-Automatik hat System-Power auf %s gesetzt (%s, SOC=%s, Reserve=%s%%)",
            "AN" if turn_on else "AUS",
            decision.reason,
            "unbekannt" if soc_percent is None else f"{soc_percent:.1f}%",
            reserve_soc_percent,
        )

    def _async_track_sunrise(self) -> Callable[[], None] | None:
        """Registriert den Sonnenaufgangs-Callback mit Fallback über `sun.sun`."""
        try:
            from homeassistant.helpers.event import async_track_sunrise

            return async_track_sunrise(self.hass, self._handle_sunrise)
        except (ImportError, AttributeError):
            _LOGGER.debug(
                "Reserve-Automatik: async_track_sunrise nicht verfügbar, "
                "nutze sun.sun Fallback"
            )

        try:
            from homeassistant.helpers.event import async_track_state_change_event
        except ImportError:
            _LOGGER.warning(
                "Reserve-Automatik kann keinen Sonnenaufgangs-Listener registrieren: "
                "Home-Assistant-Event-Helper fehlen"
            )
            return None

        def _handle_sun_state_change(event: Any) -> None:
            old_state = event.data.get("old_state")
            new_state = event.data.get("new_state")
            if new_state is None or new_state.state != "above_horizon":
                return
            if old_state is not None and old_state.state == "above_horizon":
                return
            self._handle_sunrise(self._local_now())

        return async_track_state_change_event(
            self.hass,
            "sun.sun",
            _handle_sun_state_change,
        )

    def _handle_sunrise(self, _now: datetime | None = None) -> None:
        """Callback des Event-Helpers; schaltet nach Auto-Aus wieder ein."""
        _LOGGER.debug("Reserve-Automatik: Sonnenaufgangs-Trigger ausgelöst")
        schedule_home_assistant_task(
            self.hass,
            self.async_process_coordinator_update(force_day=True),
        )

    def _backup_outage_state(self) -> tuple[bool, bool]:
        """Liest die bestehende Backup-Bewertung, ohne die Heuristik zu duplizieren."""
        evaluation = getattr(self.coordinator, "backup_evaluation", None)
        return (
            bool(getattr(evaluation, "power_outage", False)),
            bool(getattr(evaluation, "backup_active", False)),
        )

    def _daylight_or_unknown(self) -> bool:
        """
        Prüft `sun.sun` defensiv.

        Warum:
            Nur ein klarer Zustand `below_horizon` erlaubt automatisches
            Abschalten. Bei fehlender Sun-Integration bleibt die Anlage an.
        """
        states = getattr(self.hass, "states", None)
        if states is None:
            return True

        state = states.get("sun.sun")
        if state is None:
            _LOGGER.debug(
                "Reserve-Automatik: sun.sun fehlt, behandle Zustand als Tag"
            )
            return True
        return state.state != "below_horizon"

    def _command_recently_sent(self) -> bool:
        """Verhindert schnelle Wiederholung identischer Schaltversuche."""
        if self._last_command_at is None:
            return False
        return (
            self._local_now() - self._last_command_at
            < timedelta(seconds=COMMAND_THROTTLE_SECONDS)
        )

    async def _async_save_state(self, *, force: bool) -> None:
        """Speichert nur bei echten Zustandswechseln oder erzwungenem Shutdown."""
        if self._store is None or not force:
            return
        await self._store.async_save(self.state.as_dict())

    def _local_now(self) -> datetime:
        """Liefert die lokale HA-Zeit, mit robustem Fallback für Tests."""
        try:
            from homeassistant.util import dt as dt_util

            return dt_util.now()
        except ImportError:
            return datetime.now().astimezone()


def decide_system_power_reserve_guard(
    *,
    now_is_day: bool,
    soc_percent: float | None,
    reserve_soc_percent: int,
    restart_margin_percent: int,
    system_power_on: bool | None,
    auto_powered_off: bool,
    power_outage: bool = False,
    backup_active: bool = False,
) -> SystemPowerReserveGuardDecision:
    """
    Entscheidet rein fachlich, ob die Reserve-Automatik schalten soll.

    Beispiel:
        Reserve 10 %, Hysterese 2 %, Nacht:
        - SOC 10 %, System an  -> ausschalten
        - SOC 12 %, Auto-Aus   -> wieder einschalten
        - Sonnenaufgang, Auto-Aus -> wieder einschalten
    """
    if power_outage or backup_active:
        return SystemPowerReserveGuardDecision(None, "outage_or_backup_active")

    if auto_powered_off and system_power_on is True:
        return SystemPowerReserveGuardDecision(
            None,
            "system_already_on_after_auto_shutdown",
            clear_auto_powered_off=True,
        )

    if auto_powered_off:
        restart_threshold = reserve_soc_percent + restart_margin_percent
        if now_is_day:
            return SystemPowerReserveGuardDecision(
                ACTION_TURN_ON,
                "daylight_after_auto_shutdown",
            )
        if soc_percent is None:
            return SystemPowerReserveGuardDecision(None, "missing_soc")
        if soc_percent >= restart_threshold:
            return SystemPowerReserveGuardDecision(
                ACTION_TURN_ON,
                "soc_above_restart_threshold",
            )
        return SystemPowerReserveGuardDecision(
            None,
            "waiting_for_daylight_or_soc_recovery",
        )

    if soc_percent is None:
        return SystemPowerReserveGuardDecision(None, "missing_soc")
    if system_power_on is None:
        return SystemPowerReserveGuardDecision(
            None,
            "unknown_system_power_state",
        )

    if not system_power_on:
        return SystemPowerReserveGuardDecision(None, "manual_or_external_off")

    if now_is_day:
        return SystemPowerReserveGuardDecision(None, "daylight")

    if soc_percent <= reserve_soc_percent:
        return SystemPowerReserveGuardDecision(ACTION_TURN_OFF, "reserve_reached")

    return SystemPowerReserveGuardDecision(None, "reserve_not_reached")


def normalize_system_power_reserve_guard_options(
    raw_options: Mapping[str, Any],
) -> dict[str, Any]:
    """Normalisiert die Optionen der System-Power-Reserve-Automatik."""
    return {
        CONF_ENABLE_SYSTEM_POWER_RESERVE_GUARD: bool(
            raw_options.get(
                CONF_ENABLE_SYSTEM_POWER_RESERVE_GUARD,
                DEFAULT_ENABLE_SYSTEM_POWER_RESERVE_GUARD,
            )
        ),
        CONF_SYSTEM_POWER_RESERVE_RESTART_MARGIN_PERCENT: _coerce_int(
            raw_options.get(
                CONF_SYSTEM_POWER_RESERVE_RESTART_MARGIN_PERCENT,
                DEFAULT_SYSTEM_POWER_RESERVE_RESTART_MARGIN_PERCENT,
            ),
            DEFAULT_SYSTEM_POWER_RESERVE_RESTART_MARGIN_PERCENT,
            SYSTEM_POWER_RESERVE_RESTART_MARGIN_MIN,
            SYSTEM_POWER_RESERVE_RESTART_MARGIN_MAX,
        ),
    }


def _safe_total_soc_percent(data: Any) -> float | None:
    """Liest den Gesamt-SOC defensiv aus Coordinator-Daten."""
    if not data:
        return None
    try:
        return total_soc_percent(data)
    except Exception as exc:
        _LOGGER.debug("Reserve-Automatik: Gesamt-SOC nicht lesbar: %s", exc)
        return None


def _coerce_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    """Konvertiert Eingaben robust zu int und wendet Grenzen an."""
    try:
        coerced = int(float(str(value).replace(",", ".")))
    except (TypeError, ValueError):
        coerced = default
    return int(max(minimum, min(maximum, coerced)))


def _coerce_optional_str(value: Any) -> str | None:
    """Konvertiert optionale Storage-Werte robust zu str."""
    if value in (None, ""):
        return None
    return str(value)
