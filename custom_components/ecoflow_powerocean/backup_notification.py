"""
Stromausfall-Benachrichtigung fuer EcoFlow PowerOcean.

Zweck:
    Dieses Modul versendet optional eine Home-Assistant-Notify-Nachricht, wenn
    die bestehende Backup-Helper-Logik einen Stromausfall und aktive lokale
    Batterieversorgung erkennt.

Input:
    - `coordinator.backup_evaluation` aus der vorhandenen Backup-Helper-Logik
    - Options-Flow-Werte fuer Aktivierung und Notify-Entitaet

Output:
    - Eine Notify-Nachricht pro zusammenhaengender Stromausfallphase
    - Persistenter Merker in `.storage`, damit HA-Neustarts waehrend eines
      Ausfalls nicht fortlaufend doppelte Nachrichten ausloesen

Wichtige Invarianten:
    - Die Erkennung wird nicht dupliziert, sondern nutzt `BackupEvaluation`
    - Feature ist standardmaessig deaktiviert
    - Ohne aktivierte Backup Helpers wird nur geloggt und nicht gesendet

Debug-Hinweis:
    - Bei ausbleibender Nachricht zuerst die Binary-Sensoren `power_outage` und
      `backup_active` sowie deren Attribute `outage_reason` pruefen.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import logging
from typing import Any, Mapping, TYPE_CHECKING

from .backup_helpers import BackupEvaluation
from .const import (
    CONF_BACKUP_OUTAGE_NOTIFY_TARGET,
    CONF_ENABLE_BACKUP_OUTAGE_NOTIFICATION,
    DEFAULT_BACKUP_OUTAGE_NOTIFY_TARGET,
    DEFAULT_ENABLE_BACKUP_OUTAGE_NOTIFICATION,
    DOMAIN,
)
from .daily_report import (
    async_send_notification_message,
    has_notification_target,
    normalize_notification_target,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .coordinator import EcoFlowCoordinator

_LOGGER = logging.getLogger(__name__)

BACKUP_NOTIFICATION_DATA_KEY = f"{DOMAIN}_backup_notification"
STORAGE_VERSION = 1
STORAGE_KEY_PREFIX = f"{DOMAIN}.backup_outage_notification"


@dataclass(slots=True)
class BackupOutageNotificationState:
    """Persistenter Merker fuer eine zusammenhaengende Stromausfallphase."""

    notification_sent_for_active_outage: bool = False
    last_notification_iso: str | None = None
    last_recovery_iso: str | None = None

    @classmethod
    def from_mapping(
        cls,
        raw: Mapping[str, Any] | None,
    ) -> "BackupOutageNotificationState":
        """Lädt den Storage-Zustand defensiv aus beliebigen Daten."""
        if not isinstance(raw, Mapping):
            return cls()
        return cls(
            notification_sent_for_active_outage=bool(
                raw.get("notification_sent_for_active_outage", False)
            ),
            last_notification_iso=_coerce_optional_str(
                raw.get("last_notification_iso")
            ),
            last_recovery_iso=_coerce_optional_str(raw.get("last_recovery_iso")),
        )

    def as_dict(self) -> dict[str, Any]:
        """JSON-kompatible Darstellung fuer Home-Assistant-Storage."""
        return asdict(self)


class BackupOutageNotificationManager:
    """
    Home-Assistant-Anbindung fuer die optionale Stromausfall-Nachricht.

    Warum eigener Manager:
        Die Binary-Sensoren bleiben reine Zustandsanzeigen. Der Versand von
        Nachrichten ist ein Seiteneffekt und wird deshalb getrennt verwaltet.
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
        self.options = normalize_backup_outage_notification_options(entry.options)
        self.state = BackupOutageNotificationState()
        self._store: Any = None
        self._logged_missing_backup_helpers = False

    async def async_setup(self) -> None:
        """Lädt Zustand, registriert den Coordinator-Listener und prueft sofort."""
        from homeassistant.helpers.storage import Store

        self._store = Store(
            self.hass,
            STORAGE_VERSION,
            f"{STORAGE_KEY_PREFIX}.{self.entry.entry_id}",
        )
        self.state = BackupOutageNotificationState.from_mapping(
            await self._store.async_load()
        )

        remove_update_listener = self.coordinator.async_add_listener(
            self._schedule_update_from_coordinator
        )
        self.entry.async_on_unload(remove_update_listener)

        await self.async_process_coordinator_update(force_save=True)

    async def async_shutdown(self) -> None:
        """Erzwingt einen letzten Storage-Schreibvorgang beim Entladen."""
        await self._async_save_state(force=True)

    def _schedule_update_from_coordinator(self) -> None:
        """Startet die async Verarbeitung aus dem Coordinator-Listener heraus."""
        self.hass.async_create_task(self.async_process_coordinator_update())

    async def async_process_coordinator_update(
        self,
        *,
        force_save: bool = False,
    ) -> None:
        """Sendet oder rearmt die Benachrichtigung anhand der Backup-Bewertung."""
        if not bool(self.options[CONF_ENABLE_BACKUP_OUTAGE_NOTIFICATION]):
            return

        notify_target = self.options[CONF_BACKUP_OUTAGE_NOTIFY_TARGET]
        if not has_notification_target(notify_target):
            _LOGGER.warning(
                "Stromausfall-Benachrichtigung ist aktiviert, aber es ist kein "
                "Benachrichtigungsziel gesetzt"
            )
            return

        evaluation = getattr(self.coordinator, "backup_evaluation", None)
        if not isinstance(evaluation, BackupEvaluation):
            return

        if not evaluation.enabled:
            if not self._logged_missing_backup_helpers:
                _LOGGER.warning(
                    "Stromausfall-Benachrichtigung benötigt aktivierte Backup Helpers"
                )
                self._logged_missing_backup_helpers = True
            return

        if should_reset_backup_outage_notification(evaluation, self.state):
            self.state.notification_sent_for_active_outage = False
            self.state.last_recovery_iso = self._local_now().isoformat()
            await self._async_save_state(force=True)
            return

        if not should_send_backup_outage_notification(evaluation, self.state):
            await self._async_save_state(force=force_save)
            return

        title = "EcoFlow Stromausfall"
        message = build_backup_outage_notification_message(evaluation)

        try:
            await async_send_notification_message(
                self.hass,
                title,
                message,
                notify_target,
            )
        except Exception as exc:
            _LOGGER.warning(
                "Stromausfall-Benachrichtigung konnte nicht gesendet werden: %s",
                exc,
            )
            return

        self.state.notification_sent_for_active_outage = True
        self.state.last_notification_iso = self._local_now().isoformat()
        await self._async_save_state(force=True)

    async def async_send_test_notification(self) -> bool:
        """
        Sendet eine Testnachricht, ohne den Ausfall-Merker zu veraendern.

        Warum:
            Nutzer sollen das Notify-Ziel sofort pruefen koennen, ohne auf einen
            echten Stromausfall angewiesen zu sein.
        """
        if not bool(self.options[CONF_ENABLE_BACKUP_OUTAGE_NOTIFICATION]):
            _LOGGER.warning(
                "Stromausfall-Test wurde angefordert, aber das Feature ist deaktiviert"
            )
            return False

        notify_target = self.options[CONF_BACKUP_OUTAGE_NOTIFY_TARGET]
        if not has_notification_target(notify_target):
            _LOGGER.warning(
                "Stromausfall-Test kann nicht senden: kein Benachrichtigungsziel gesetzt"
            )
            return False

        evaluation = getattr(self.coordinator, "backup_evaluation", None)
        message = build_backup_outage_test_message(evaluation)

        try:
            await async_send_notification_message(
                self.hass,
                "EcoFlow Stromausfall-Test",
                message,
                notify_target,
            )
        except Exception as exc:
            _LOGGER.warning(
                "Stromausfall-Testbenachrichtigung konnte nicht gesendet werden: %s",
                exc,
            )
            return False
        return True

    async def _async_save_state(self, *, force: bool) -> None:
        """Speichert den Benachrichtigungsmerker bei wichtigen Zustandswechseln."""
        if self._store is None or not force:
            return
        await self._store.async_save(self.state.as_dict())

    def _local_now(self) -> datetime:
        """Liefert die lokale HA-Zeit, mit robustem Fallback fuer Tests."""
        try:
            from homeassistant.util import dt as dt_util

            return dt_util.now()
        except ImportError:
            return datetime.now().astimezone()


def normalize_backup_outage_notification_options(
    raw_options: Mapping[str, Any],
) -> dict[str, Any]:
    """Normalisiert die Optionen fuer Stromausfall-Benachrichtigungen."""
    return {
        CONF_ENABLE_BACKUP_OUTAGE_NOTIFICATION: bool(
            raw_options.get(
                CONF_ENABLE_BACKUP_OUTAGE_NOTIFICATION,
                DEFAULT_ENABLE_BACKUP_OUTAGE_NOTIFICATION,
            )
        ),
        CONF_BACKUP_OUTAGE_NOTIFY_TARGET: normalize_notification_target(
            raw_options.get(
                CONF_BACKUP_OUTAGE_NOTIFY_TARGET,
                DEFAULT_BACKUP_OUTAGE_NOTIFY_TARGET,
            )
        ),
    }


def should_send_backup_outage_notification(
    evaluation: BackupEvaluation,
    state: BackupOutageNotificationState,
) -> bool:
    """Prueft, ob fuer diese Ausfallphase eine neue Nachricht faellig ist."""
    return (
        evaluation.enabled
        and evaluation.power_outage
        and evaluation.backup_active
        and not state.notification_sent_for_active_outage
    )


def should_reset_backup_outage_notification(
    evaluation: BackupEvaluation,
    state: BackupOutageNotificationState,
) -> bool:
    """Schaltet den Merker nach Ende der Ausfallphase fuer das naechste Ereignis frei."""
    return (
        state.notification_sent_for_active_outage
        and not evaluation.power_outage
        and not evaluation.backup_active
    )


def build_backup_outage_notification_message(evaluation: BackupEvaluation) -> str:
    """Erzeugt den deutsch formatierten Nachrichtentext."""
    return (
        "Stromausfall erkannt: Die Anlage versorgt das Haus ueber Batterie/PV.\n"
        f"Geschätzte Backup-Laufzeit: {_format_runtime(evaluation)}\n"
        f"Nutzbare Backup-Energie: {_format_usable_energy(evaluation)}\n"
        f"Empfohlene Aktion: {_format_recommended_action(evaluation.recommended_action)}"
    )


def build_backup_outage_test_message(evaluation: Any) -> str:
    """Erzeugt eine Testnachricht mit aktuellem Backup-Status."""
    if not isinstance(evaluation, BackupEvaluation) or not evaluation.enabled:
        return (
            "Testnachricht: Die Stromausfall-Benachrichtigung ist eingerichtet.\n"
            "Aktueller Backup-Status: Backup Helpers liefern noch keine Bewertung."
        )

    if evaluation.power_outage and evaluation.backup_active:
        status = "Stromausfall erkannt, Batterieversorgung aktiv"
    elif evaluation.power_outage:
        status = "Stromausfall erkannt, Batterieversorgung noch nicht bestaetigt"
    else:
        status = "Kein Stromausfall erkannt"

    return (
        "Testnachricht: Die Stromausfall-Benachrichtigung ist eingerichtet.\n"
        f"Aktueller Backup-Status: {status}\n"
        f"Erkennungsgrund: {evaluation.outage_reason}\n"
        f"Geschätzte Backup-Laufzeit: {_format_runtime(evaluation)}"
    )


def _format_runtime(evaluation: BackupEvaluation) -> str:
    """Formatiert die geschaetzte Restlaufzeit."""
    if evaluation.runtime_estimate_minutes is None:
        return "unbekannt"
    minutes = max(int(evaluation.runtime_estimate_minutes), 0)
    hours, remaining_minutes = divmod(minutes, 60)
    if hours <= 0:
        return f"{remaining_minutes} min"
    return f"{hours} h {remaining_minutes:02d} min"


def _format_usable_energy(evaluation: BackupEvaluation) -> str:
    """Formatiert die nutzbare Backup-Energie."""
    if evaluation.usable_energy_wh is None:
        return "unbekannt"
    return f"{evaluation.usable_energy_wh / 1000.0:.2f}".replace(".", ",") + " kWh"


def _format_recommended_action(action: str) -> str:
    """Uebersetzt die technische Aktion in kurze Nutzersprache."""
    return {
        "normal": "Normal weiter beobachten",
        "shed_load": "Last reduzieren",
        "shutdown_recommended": "Shutdown vorbereiten",
        "unknown": "Unbekannt",
    }.get(action, "Unbekannt")


def _coerce_optional_str(value: Any) -> str | None:
    """Konvertiert optionale Storage-Werte robust zu str."""
    if value in (None, ""):
        return None
    return str(value)
