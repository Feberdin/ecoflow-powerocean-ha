"""
Täglicher Sonnenuntergangsbericht für EcoFlow PowerOcean.

Zweck:
    Dieses Modul kapselt das optionale Komfort-Feature, das bei Sonnenuntergang
    eine Home-Assistant-Nachricht mit Tages-Einspeisung, berechneter Vergütung
    und Akku-100%-Dauer versendet.

Input:
    - Coordinator-Daten der Integration
    - Options-Flow-Werte für Ziel und Einspeisetarif
    - Lokale Home-Assistant-Zeit für Tageswechsel und Sonnenuntergang

Output:
    - Persistenter Tages-Zwischenstand in `.storage`
    - Eine optionale `notify.send_message` Nachricht pro lokalem Datum

Wichtige Invarianten:
    - Das Feature ist standardmäßig deaktiviert
    - Keine neuen Entity-IDs, keine Änderungen an Energie-Dashboard-Sensoren
    - Lange HA-/MQTT-Lücken werden defensiv begrenzt, damit keine unrealistischen
      Akku-100%-Zeiten oder Einspeisemengen entstehen

Debug-Hinweis:
    - Bei fehlendem oder ungültigem Notify-Target wird nur geloggt
    - Storage-Schreibzugriffe werden gedrosselt und beim Sunset/Unload erzwungen
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import logging
import math
from typing import Any, Callable, Mapping, TYPE_CHECKING

from .backup_helpers import grid_power_w, total_soc_percent
from .const import (
    CONF_DAILY_REPORT_FEED_IN_TARIFF_EUR_PER_KWH,
    CONF_DAILY_REPORT_NOTIFY_TARGET,
    CONF_ENABLE_DAILY_SUNSET_REPORT,
    DAILY_REPORT_FEED_IN_TARIFF_MAX,
    DAILY_REPORT_FEED_IN_TARIFF_MIN,
    DEFAULT_DAILY_REPORT_FEED_IN_TARIFF_EUR_PER_KWH,
    DEFAULT_DAILY_REPORT_NOTIFY_TARGET,
    DEFAULT_ENABLE_DAILY_SUNSET_REPORT,
    DOMAIN,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .coordinator import EcoFlowCoordinator

_LOGGER = logging.getLogger(__name__)

DAILY_REPORT_DATA_KEY = f"{DOMAIN}_daily_report"
STORAGE_VERSION = 1
STORAGE_KEY_PREFIX = f"{DOMAIN}.daily_report"
STORE_SAVE_THROTTLE_SECONDS = 60

# Warum:
# Bei MQTT-/HA-Ausfällen kann der letzte bekannte Zustand lange alt sein. Für
# die Komfortauswertung ist eine vorsichtige Tagesnähe sinnvoller als eine
# scheinbar exakte, aber potenziell stark überhöhte Fortschreibung.
MAX_FULL_SOC_COUNT_GAP_SECONDS = 10 * 60
MAX_EXPORT_INTEGRATION_GAP_SECONDS = 30 * 60

NOTIFY_TARGET_KEYS = ("entity_id", "device_id", "area_id", "label_id")


@dataclass(slots=True)
class DailyReportState:
    """
    Persistenter Tageszustand für den Sonnenuntergangsbericht.

    Beispiel:
        Input: 1000 W Einspeisung über 30 Minuten, SOC 100 %
        Output: daily_export_kwh=0.5, battery_full_seconds=1800
    """

    local_date: str
    daily_export_kwh: float = 0.0
    battery_full_seconds: float = 0.0
    last_update_iso: str | None = None
    last_export_power_w: float | None = None
    last_soc_percent: float | None = None
    last_sent_date: str | None = None

    @classmethod
    def from_mapping(
        cls,
        raw: Mapping[str, Any] | None,
        *,
        default_local_date: str,
    ) -> "DailyReportState":
        """Lädt gespeicherten Zustand robust aus beliebigen Storage-Daten."""
        if not isinstance(raw, Mapping):
            return cls(local_date=default_local_date)

        return cls(
            local_date=str(raw.get("local_date") or default_local_date),
            daily_export_kwh=_coerce_float(raw.get("daily_export_kwh"), 0.0),
            battery_full_seconds=_coerce_float(
                raw.get("battery_full_seconds"),
                0.0,
            ),
            last_update_iso=_coerce_optional_str(raw.get("last_update_iso")),
            last_export_power_w=_coerce_optional_float(
                raw.get("last_export_power_w")
            ),
            last_soc_percent=_coerce_optional_float(raw.get("last_soc_percent")),
            last_sent_date=_coerce_optional_str(raw.get("last_sent_date")),
        )

    def as_dict(self) -> dict[str, Any]:
        """JSON-kompatible Darstellung für Home-Assistant-Storage."""
        return asdict(self)


class DailyReportAccumulator:
    """
    Reine Python-Logik für Tages-Einspeisung und Akku-100%-Dauer.

    Warum:
        Diese Klasse enthält keine Home-Assistant-Imports. Dadurch ist die
        fachliche Logik einfach testbar und unabhängig von HA-Laufzeitdetails.
    """

    def __init__(self, state: DailyReportState) -> None:
        self.state = state

    def reset_for_date(self, local_date: str) -> None:
        """Startet einen neuen lokalen Kalendertag und behält `last_sent_date`."""
        self.state = DailyReportState(
            local_date=local_date,
            last_sent_date=self.state.last_sent_date,
        )

    @staticmethod
    def should_count_full_soc(soc_percent: float | None) -> bool:
        """Zählt nur eindeutig volle Akkustände."""
        return soc_percent is not None and soc_percent >= 100.0

    def update(
        self,
        now: datetime,
        *,
        export_power_w: float,
        soc_percent: float | None,
    ) -> None:
        """
        Aktualisiert den Tagesstand per Links-Riemann-Summe.

        Warum Links-Riemann:
            Die MQTT-Daten liefern Momentanleistung. Wir rechnen daher die
            zuletzt bekannte Leistung über das vergangene Intervall fort.
        """
        local_date = now.date().isoformat()
        if self.state.local_date != local_date:
            self.reset_for_date(local_date)

        export_power = max(_coerce_float(export_power_w, 0.0), 0.0)
        soc = _coerce_optional_float(soc_percent)
        previous_update = _parse_datetime(self.state.last_update_iso, now)

        if previous_update is not None:
            delta_seconds = (now - previous_update).total_seconds()
            if delta_seconds > 0:
                self._integrate_export(delta_seconds)
                self._integrate_full_soc(delta_seconds)

        self.state.last_update_iso = now.isoformat()
        self.state.last_export_power_w = export_power
        self.state.last_soc_percent = soc

    def mark_sent(self) -> None:
        """Markiert den aktuellen lokalen Tag als erfolgreich gemeldet."""
        self.state.last_sent_date = self.state.local_date

    def _integrate_export(self, delta_seconds: float) -> None:
        """Integriert die vorherige Einspeiseleistung defensiv in kWh."""
        if self.state.last_export_power_w is None:
            return
        counted_seconds = min(delta_seconds, MAX_EXPORT_INTEGRATION_GAP_SECONDS)
        previous_power_w = max(float(self.state.last_export_power_w), 0.0)
        self.state.daily_export_kwh += (previous_power_w / 1000.0) * (
            counted_seconds / 3600.0
        )

    def _integrate_full_soc(self, delta_seconds: float) -> None:
        """Zählt Akku-100%-Zeit anhand des vorherigen SOC-Zustands."""
        if not self.should_count_full_soc(self.state.last_soc_percent):
            return
        self.state.battery_full_seconds += min(
            delta_seconds,
            MAX_FULL_SOC_COUNT_GAP_SECONDS,
        )


class DailySunsetReportManager:
    """
    Home-Assistant-Anbindung für den optionalen Sonnenuntergangsbericht.

    Der Manager hängt sich an Coordinator-Updates und an den Sunset-Event. Er
    verändert die bestehende `hass.data[DOMAIN][entry.entry_id]` Struktur nicht.
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
        self.options = normalize_daily_report_options(entry.options)
        self._store: Any = None
        self._last_store_save_at: float | None = None

        local_date = self._local_now().date().isoformat()
        self.accumulator = DailyReportAccumulator(
            DailyReportState(local_date=local_date)
        )

    async def async_setup(self) -> None:
        """Lädt Zustand, registriert Listener und verarbeitet vorhandene Daten."""
        from homeassistant.helpers.storage import Store

        self._store = Store(
            self.hass,
            STORAGE_VERSION,
            f"{STORAGE_KEY_PREFIX}.{self.entry.entry_id}",
        )

        local_date = self._local_now().date().isoformat()
        stored = await self._store.async_load()
        state = DailyReportState.from_mapping(stored, default_local_date=local_date)
        self.accumulator = DailyReportAccumulator(state)
        if self.accumulator.state.local_date != local_date:
            self.accumulator.reset_for_date(local_date)

        remove_update_listener = self.coordinator.async_add_listener(
            self._schedule_update_from_coordinator
        )
        self.entry.async_on_unload(remove_update_listener)

        remove_sunset_listener = self._async_track_sunset()
        if remove_sunset_listener is not None:
            self.entry.async_on_unload(remove_sunset_listener)

        await self.async_process_coordinator_update(force_save=True)

    async def async_shutdown(self) -> None:
        """Erzwingt einen letzten Storage-Schreibvorgang beim Entladen."""
        await self._async_save_state(force=True)

    def _schedule_update_from_coordinator(self) -> None:
        """Startet die async Verarbeitung aus dem Coordinator-Listener heraus."""
        self.hass.async_create_task(self.async_process_coordinator_update())

    async def async_process_coordinator_update(self, *, force_save: bool = False) -> None:
        """Übernimmt den aktuellen Coordinator-Stand in den Tages-Akkumulator."""
        data = getattr(self.coordinator, "data", None)
        if not data:
            return

        try:
            export_power_w = max(-grid_power_w(data), 0.0)
            soc_percent = total_soc_percent(data)
        except Exception as exc:
            _LOGGER.debug("Tagesbericht: Daten konnten nicht gelesen werden: %s", exc)
            return

        self.accumulator.update(
            self._local_now(),
            export_power_w=export_power_w,
            soc_percent=soc_percent,
        )
        await self._async_save_state(force=force_save)

    async def async_send_sunset_report(self) -> None:
        """Sendet den Tagesbericht genau einmal pro lokalem Datum."""
        if not bool(self.options[CONF_ENABLE_DAILY_SUNSET_REPORT]):
            return

        notify_target = self.options[CONF_DAILY_REPORT_NOTIFY_TARGET]
        if not has_notification_target(notify_target):
            _LOGGER.warning(
                "Tagesbericht ist aktiviert, aber es ist kein Benachrichtigungsziel gesetzt"
            )
            return

        local_date = self._local_now().date().isoformat()
        if self.accumulator.state.local_date != local_date:
            self.accumulator.reset_for_date(local_date)

        await self.async_process_coordinator_update(force_save=False)

        state = self.accumulator.state
        if state.last_sent_date == state.local_date:
            _LOGGER.debug("Tagesbericht für %s wurde bereits gesendet", state.local_date)
            return

        title = "EcoFlow Tagesbericht"
        message = build_daily_report_message(
            state,
            tariff_eur_per_kwh=float(
                self.options[CONF_DAILY_REPORT_FEED_IN_TARIFF_EUR_PER_KWH]
            ),
            has_enough_data=state.last_update_iso is not None,
        )

        try:
            await self._async_send_notification(title, message, notify_target)
        except Exception as exc:
            _LOGGER.warning("Tagesbericht konnte nicht gesendet werden: %s", exc)
            return

        self.accumulator.mark_sent()
        await self._async_save_state(force=True)

    async def async_send_test_report(self) -> bool:
        """
        Sendet sofort einen Testbericht, ohne den Tag als gemeldet zu markieren.

        Warum:
            Der Sonnenuntergangsbericht laeuft nur einmal taeglich. Ein separater
            Testpfad macht Notify-Ziel, Service-Aufruf und Datenformat pruefbar,
            ohne den echten Tagesbericht fuer den Abend zu blockieren.
        """
        if not bool(self.options[CONF_ENABLE_DAILY_SUNSET_REPORT]):
            _LOGGER.warning(
                "Tagesbericht-Test wurde angefordert, aber das Feature ist deaktiviert"
            )
            return False

        notify_target = self.options[CONF_DAILY_REPORT_NOTIFY_TARGET]
        if not has_notification_target(notify_target):
            _LOGGER.warning(
                "Tagesbericht-Test kann nicht senden: kein Benachrichtigungsziel gesetzt"
            )
            return False

        local_date = self._local_now().date().isoformat()
        if self.accumulator.state.local_date != local_date:
            self.accumulator.reset_for_date(local_date)

        await self.async_process_coordinator_update(force_save=False)

        state = self.accumulator.state
        title = "EcoFlow Tagesbericht (Test)"
        message = build_daily_report_message(
            state,
            tariff_eur_per_kwh=float(
                self.options[CONF_DAILY_REPORT_FEED_IN_TARIFF_EUR_PER_KWH]
            ),
            has_enough_data=state.last_update_iso is not None,
        )

        try:
            await self._async_send_notification(title, message, notify_target)
        except Exception as exc:
            _LOGGER.warning("Tagesbericht-Test konnte nicht gesendet werden: %s", exc)
            return False

        await self._async_save_state(force=True)
        return True

    def _async_track_sunset(self) -> Callable[[], None] | None:
        """
        Registriert den Sunset-Callback.

        `async_track_sunset` ist laut HA-Developer-Dokumentation der bevorzugte
        Event-Helper. Der Fallback nutzt `sun.sun`, falls ein älteres Runtime-
        Umfeld den Helper nicht bereitstellt.
        """
        try:
            from homeassistant.helpers.event import async_track_sunset

            return async_track_sunset(self.hass, self._handle_sunset)
        except (ImportError, AttributeError):
            _LOGGER.debug(
                "async_track_sunset nicht verfügbar, nutze sun.sun Fallback"
            )

        try:
            from homeassistant.helpers.event import async_track_state_change_event
        except ImportError:
            _LOGGER.warning(
                "Tagesbericht kann keinen Sunset-Listener registrieren: "
                "Home-Assistant-Event-Helper fehlen"
            )
            return None

        def _handle_sun_state_change(event: Any) -> None:
            old_state = event.data.get("old_state")
            new_state = event.data.get("new_state")
            if new_state is None or new_state.state != "below_horizon":
                return
            if old_state is not None and old_state.state == "below_horizon":
                return
            self._handle_sunset(self._local_now())

        return async_track_state_change_event(
            self.hass,
            "sun.sun",
            _handle_sun_state_change,
        )

    def _handle_sunset(self, _now: datetime) -> None:
        """Callback des Event-Helpers; sendet asynchron und blockiert HA nicht."""
        self.hass.async_create_task(self.async_send_sunset_report())

    async def _async_send_notification(
        self,
        title: str,
        message: str,
        notify_target: Mapping[str, Any],
    ) -> None:
        """Sendet bevorzugt per `notify.send_message` mit Target-Block."""
        service_data = {
            "title": title,
            "message": message,
        }
        try:
            await self.hass.services.async_call(
                "notify",
                "send_message",
                service_data,
                target=dict(notify_target),
                blocking=False,
            )
            return
        except TypeError:
            # Ältere HA-Versionen akzeptieren den `target` Parameter ggf. nicht
            # an dieser Stelle. Dann werden Target-Felder in die Service-Daten
            # gemerged. Wenn auch das fehlschlägt, loggt der Aufrufer sauber.
            fallback_data = dict(service_data)
            fallback_data.update(_target_to_service_data(notify_target))
            await self.hass.services.async_call(
                "notify",
                "send_message",
                fallback_data,
                blocking=False,
            )

    async def _async_save_state(self, *, force: bool) -> None:
        """Speichert gedrosselt, außer ein Sunset/Unload erzwingt Persistenz."""
        if self._store is None:
            return

        now_ts = self._local_now().timestamp()
        if (
            not force
            and self._last_store_save_at is not None
            and now_ts - self._last_store_save_at < STORE_SAVE_THROTTLE_SECONDS
        ):
            return

        await self._store.async_save(self.accumulator.state.as_dict())
        self._last_store_save_at = now_ts

    def _local_now(self) -> datetime:
        """Liefert die lokale HA-Zeit, mit robustem Fallback für Tests."""
        try:
            from homeassistant.util import dt as dt_util

            return dt_util.now()
        except ImportError:
            return datetime.now().astimezone()


def normalize_daily_report_options(raw_options: Mapping[str, Any]) -> dict[str, Any]:
    """Normalisiert alle Daily-Report-Optionen an einer zentralen Stelle."""
    return {
        CONF_ENABLE_DAILY_SUNSET_REPORT: bool(
            raw_options.get(
                CONF_ENABLE_DAILY_SUNSET_REPORT,
                DEFAULT_ENABLE_DAILY_SUNSET_REPORT,
            )
        ),
        CONF_DAILY_REPORT_NOTIFY_TARGET: _normalize_notify_target(
            raw_options.get(
                CONF_DAILY_REPORT_NOTIFY_TARGET,
                DEFAULT_DAILY_REPORT_NOTIFY_TARGET,
            )
        ),
        CONF_DAILY_REPORT_FEED_IN_TARIFF_EUR_PER_KWH: normalize_feed_in_tariff(
            raw_options.get(
                CONF_DAILY_REPORT_FEED_IN_TARIFF_EUR_PER_KWH,
                DEFAULT_DAILY_REPORT_FEED_IN_TARIFF_EUR_PER_KWH,
            )
        ),
    }


def normalize_feed_in_tariff(value: Any) -> float:
    """Konvertiert und begrenzt die Einspeisevergütung defensiv."""
    tariff = _coerce_float(value, DEFAULT_DAILY_REPORT_FEED_IN_TARIFF_EUR_PER_KWH)
    return max(
        DAILY_REPORT_FEED_IN_TARIFF_MIN,
        min(DAILY_REPORT_FEED_IN_TARIFF_MAX, tariff),
    )


def has_notification_target(target: Any) -> bool:
    """Prüft, ob eine Notify-Entität oder ein Target-Dict vorhanden ist."""
    target = _normalize_notify_target(target)

    for key in NOTIFY_TARGET_KEYS:
        if _has_value(target.get(key)):
            return True
    return any(_has_value(value) for value in target.values())


def notification_target_entity_id(target: Any) -> str | None:
    """Extrahiert eine einzelne Notify-Entität für den Options-Flow-Default."""
    target = _normalize_notify_target(target)
    entity_id = target.get("entity_id")
    if isinstance(entity_id, str) and entity_id.strip():
        return entity_id
    if isinstance(entity_id, list) and entity_id:
        first = entity_id[0]
        return first if isinstance(first, str) and first.strip() else None
    return None


def calculate_report_value_eur(
    daily_export_kwh: float,
    tariff_eur_per_kwh: float,
) -> float:
    """Berechnet den Tageswert aus kWh und Tarif."""
    return daily_export_kwh * tariff_eur_per_kwh


def format_duration(seconds: float) -> str:
    """Formatiert Sekunden als `MM min` oder `H h MM min`."""
    total_minutes = int(max(seconds, 0.0) // 60)
    hours, minutes = divmod(total_minutes, 60)
    if hours <= 0:
        return f"{minutes} min"
    return f"{hours} h {minutes:02d} min"


def build_daily_report_message(
    state: DailyReportState,
    *,
    tariff_eur_per_kwh: float,
    has_enough_data: bool,
) -> str:
    """Erzeugt den deutsch formatierten Nachrichtentext."""
    if not has_enough_data:
        return "Heute liegen noch nicht genug Daten für einen Tagesbericht vor."

    tariff = normalize_feed_in_tariff(tariff_eur_per_kwh)
    value_eur = calculate_report_value_eur(state.daily_export_kwh, tariff)
    return (
        f"Heute eingespeist: {_format_de(state.daily_export_kwh, 2)} kWh\n"
        f"Vergütung: {_format_de(value_eur, 2)} € "
        f"({_format_de(tariff, 4)} €/kWh)\n"
        f"Akku bei 100 %: {format_duration(state.battery_full_seconds)}"
    )


def _normalize_notify_target(value: Any) -> dict[str, Any]:
    """Macht Notify-Entity- oder TargetSelector-Werte servicefähig."""
    if isinstance(value, str):
        stripped = value.strip()
        return {"entity_id": stripped} if stripped else {}
    if isinstance(value, list):
        entity_ids = [item for item in value if isinstance(item, str) and item.strip()]
        return {"entity_id": entity_ids} if entity_ids else {}
    if not isinstance(value, Mapping):
        return {}
    return {str(key): val for key, val in value.items() if _has_value(val)}


def _target_to_service_data(target: Mapping[str, Any]) -> dict[str, Any]:
    """Bereitet Target-Felder für ältere Service-Aufrufpfade auf."""
    return {
        str(key): value
        for key, value in target.items()
        if key in NOTIFY_TARGET_KEYS and _has_value(value)
    }


def _has_value(value: Any) -> bool:
    """Bewertet leere Target-Felder robust."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _coerce_float(value: Any, default: float) -> float:
    """Konvertiert beliebige Werte robust zu float."""
    if isinstance(value, str):
        value = value.strip().replace(",", ".")
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _coerce_optional_float(value: Any) -> float | None:
    """Konvertiert optionale Werte robust zu float."""
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _coerce_optional_str(value: Any) -> str | None:
    """Konvertiert optionale Storage-Werte robust zu str."""
    if value in (None, ""):
        return None
    return str(value)


def _parse_datetime(value: str | None, reference: datetime) -> datetime | None:
    """Parst ISO-Zeitpunkte und gleicht naive/aware Zeitstempel an."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None

    if parsed.tzinfo is None and reference.tzinfo is not None:
        return parsed.replace(tzinfo=reference.tzinfo)
    if parsed.tzinfo is not None and reference.tzinfo is None:
        return parsed.replace(tzinfo=None)
    return parsed


def _format_de(value: float, digits: int) -> str:
    """Formatiert Dezimalwerte mit deutschem Komma."""
    return f"{value:.{digits}f}".replace(".", ",")
