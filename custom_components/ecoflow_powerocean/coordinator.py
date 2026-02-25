"""
DataUpdateCoordinator für die EcoFlow PowerOcean Plus Integration.

Dieser Coordinator verwaltet den gesamten Kommunikationszyklus:

    1. Authentifizierung bei der EcoFlow Cloud API (HTTPS, Base64-Passwort)
    2. Abruf zeitlich begrenzter MQTT-Zugangsdaten
    3. Aufbau einer TLS-gesicherten MQTT-Verbindung zum EcoFlow Cloud-Broker
    4. Empfang und Dekodierung von Protobuf-Nachrichten im Hintergrund-Thread
    5. Weiterleitung dekodierter Daten an Home Assistant über async_set_updated_data()

Kommunikationsmodell:
    Das Gerät (PowerOcean Plus) sendet seine Statusdaten selbständig über MQTT
    (Push-Modell). Zusätzlich kann eine GET-Anfrage gesendet werden, um sofortige
    Datenpakete auszulösen. Die Home Assistant Sensoren werden bei jeder eingehenden
    MQTT-Nachricht automatisch aktualisiert.

MQTT-Verbindung:
    Broker: mqtt-e.ecoflow.com:8883 (TLS)
    Topic:  /app/device/property/{SERIAL_NUMBER}

Datenschutz-Hinweis:
    Zugangsdaten werden ausschließlich in der Home Assistant Config Entry gespeichert
    und niemals in Logs ausgegeben.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import ssl
import threading
import uuid
from datetime import timedelta
from typing import Any

import aiohttp

try:
    import paho.mqtt.client as mqtt
    from paho.mqtt.enums import CallbackAPIVersion
    _PAHO_V2 = True
except ImportError:
    import paho.mqtt.client as mqtt  # type: ignore[no-redef]
    _PAHO_V2 = False

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    API_CERT_URL,
    API_LOGIN_URL,
    API_TIMEOUT,
    CMD_FUNC_EMS,
    CMD_ID_BP_STATUS,
    CMD_ID_ENERGY_STREAM,
    CONF_SERIAL_NUMBER,
    DATA_BATTERIES,
    DATA_EMS_HEARTBEAT,
    DATA_ENERGY_STREAM,
    DOMAIN,
    MQTT_HOST,
    MQTT_KEEPALIVE,
    MQTT_PORT,
    MQTT_RECONNECT_DELAY,
    TOPIC_DEVICE_PROPERTY,
    TOPIC_GET,
)
from .proto_decoder import BatteryPackData, EmsHeartbeatData, EnergyStreamData, decode_mqtt_payload

_LOGGER = logging.getLogger(__name__)


class EcoFlowCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """
    Koordiniert alle Datenabrufe für eine EcoFlow PowerOcean Plus Anlage.

    Der Coordinator verbindet sich einmalig beim Setup mit dem EcoFlow MQTT-Broker
    und empfängt danach kontinuierlich Statusupdates. Ein periodischer Fallback-Refresh
    sendet alle 5 Minuten eine GET-Anfrage, falls das Gerät keine spontanen Updates sendet.

    Attributes:
        serial_number: Seriennummer des PowerOcean Plus Geräts.
        device_info:   HA-Geräteinformationen für die Sensor-Entitäten.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """
        Initialisiert den Coordinator.

        Args:
            hass:  Home Assistant Instanz.
            entry: Config Entry mit Zugangsdaten und Seriennummer.
        """
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.data[CONF_SERIAL_NUMBER]}",
            update_interval=timedelta(minutes=5),
        )

        self._entry = entry
        self._email: str = entry.data[CONF_EMAIL]
        self._password: str = entry.data[CONF_PASSWORD]
        self.serial_number: str = entry.data[CONF_SERIAL_NUMBER]

        # API-Zustand
        self._token: str | None = None
        self._user_id: str | None = None

        # MQTT-Zustand
        self._mqtt_user: str | None = None
        self._mqtt_password: str | None = None
        self._mqtt_client: mqtt.Client | None = None
        self._mqtt_connected: bool = False
        self._mqtt_lock = threading.Lock()

        # Initialer Datensatz
        self.data: dict[str, Any] = {
            DATA_BATTERIES: {},
            DATA_ENERGY_STREAM: None,
            DATA_EMS_HEARTBEAT: None,
        }

    # ── Öffentliche Setup-Methode ─────────────────────────────────────────────

    async def async_setup(self) -> None:
        """
        Initialisiert die Verbindung zur EcoFlow Cloud.

        Wird einmalig von async_setup_entry() aufgerufen. Führt Login,
        MQTT-Credential-Abruf und MQTT-Verbindungsaufbau durch.

        Raises:
            ConfigEntryAuthFailed: Bei falschen Zugangsdaten.
            ConfigEntryNotReady:   Bei Netzwerkproblemen oder Timeout.
        """
        await self._async_login()
        await self._async_get_mqtt_credentials()
        await self.hass.async_add_executor_job(self._setup_mqtt_client)

    # ── Authentication ────────────────────────────────────────────────────────

    async def _async_login(self) -> None:
        """
        Authentifiziert sich bei der EcoFlow Cloud API.

        Das Passwort wird Base64-kodiert übertragen — nicht gehasht und nicht
        im Klartext. Dies ist das von der EcoFlow App verwendete Verfahren.

        Raises:
            ConfigEntryAuthFailed: Bei Code 2026 (falsche Zugangsdaten).
            ConfigEntryNotReady:   Bei Netzwerkfehlern oder Timeout.
        """
        password_b64 = base64.b64encode(self._password.encode("utf-8")).decode("utf-8")
        payload = {
            "email": self._email,
            "password": password_b64,
            "scene": "IOT_APP",
            "userType": "ECOFLOW",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    API_LOGIN_URL,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=API_TIMEOUT),
                ) as resp:
                    data = await resp.json()
        except aiohttp.ClientError as exc:
            raise ConfigEntryNotReady(f"Netzwerkfehler beim Login: {exc}") from exc
        except asyncio.TimeoutError as exc:
            raise ConfigEntryNotReady("Login-Anfrage hat Timeout überschritten.") from exc

        code = str(data.get("code", ""))
        if code != "0":
            msg = data.get("message", "Unbekannter Fehler")
            if code == "2026":
                raise ConfigEntryAuthFailed(
                    f"EcoFlow Login fehlgeschlagen: {msg}. "
                    "Bitte E-Mail und Passwort prüfen."
                )
            raise ConfigEntryNotReady(f"EcoFlow API Fehler {code}: {msg}")

        user_data = data.get("data", {})
        self._token = user_data.get("token")
        self._user_id = str(user_data.get("user", {}).get("userId", ""))
        _LOGGER.debug("EcoFlow Login erfolgreich (UserID: %s)", self._user_id)

    async def _async_get_mqtt_credentials(self) -> None:
        """
        Ruft zeitlich begrenzte MQTT-Zugangsdaten von der EcoFlow API ab.

        Die Zugangsdaten (certificateAccount + certificatePassword) sind
        temporär und werden bei Bedarf erneuert. Sie ermöglichen die
        Verbindung zum EcoFlow Cloud MQTT-Broker.

        Raises:
            ConfigEntryNotReady: Bei API-Fehler oder Netzwerkproblem.
        """
        headers = {"Authorization": f"Bearer {self._token}"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    API_CERT_URL,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=API_TIMEOUT),
                ) as resp:
                    data = await resp.json()
        except aiohttp.ClientError as exc:
            raise ConfigEntryNotReady(f"Netzwerkfehler beim Abrufen der MQTT-Certs: {exc}") from exc

        if str(data.get("code", "")) != "0":
            msg = data.get("message", "Unbekannter Fehler")
            raise ConfigEntryNotReady(f"MQTT-Credential-Fehler: {msg}")

        cert = data.get("data", {})
        self._mqtt_user = cert.get("certificateAccount")
        self._mqtt_password = cert.get("certificatePassword")
        _LOGGER.debug("MQTT-Zugangsdaten erhalten (Account: %s)", self._mqtt_user)

    # ── MQTT Setup (läuft im Executor-Thread) ─────────────────────────────────

    def _setup_mqtt_client(self) -> None:
        """
        Erstellt und startet den MQTT-Client.

        Wird im Executor-Thread ausgeführt, da paho-mqtt's connect()-Methode
        blockierend ist. Der Client läuft danach in seinem eigenen Hintergrund-Thread
        (loop_start()) und kommuniziert über Callbacks mit HA.

        Verwendet das Format "ANDROID_{uuid}_{user_id}" als Client-ID — genau
        wie die EcoFlow App. Andere Formate werden mit "Not authorized" abgelehnt.
        """
        # EcoFlow's Broker erwartet das Format "ANDROID_{uuid}_{user_id}".
        # Die UUID wird deterministisch aus der Seriennummer abgeleitet, damit
        # nach jedem HA-Neustart dieselbe Client-ID verwendet wird und das
        # EcoFlow-Limit von 10 neuen Client-IDs pro Tag nicht überschritten wird.
        stable_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, f"ha-ecoflow-{self.serial_number}")
        client_id = f"ANDROID_{stable_uuid}_{self._user_id}"

        if _PAHO_V2:
            client = mqtt.Client(
                client_id=client_id,
                callback_api_version=CallbackAPIVersion.VERSION2,
            )
        else:
            client = mqtt.Client(client_id=client_id)

        # TLS aktivieren (Zertifikat nicht verifizieren — EcoFlow-Standard)
        client.tls_set(cert_reqs=ssl.CERT_NONE)
        client.tls_insecure_set(True)

        # Authentifizierung
        client.username_pw_set(self._mqtt_user, self._mqtt_password)

        # Callbacks registrieren
        client.on_connect = self._on_mqtt_connect
        client.on_message = self._on_mqtt_message
        client.on_disconnect = self._on_mqtt_disconnect

        # Automatisches Reconnect konfigurieren
        client.reconnect_delay_set(min_delay=5, max_delay=MQTT_RECONNECT_DELAY)

        self._mqtt_client = client

        try:
            client.connect(MQTT_HOST, MQTT_PORT, keepalive=MQTT_KEEPALIVE)
            client.loop_start()
            _LOGGER.info(
                "MQTT-Verbindungsaufbau gestartet zu %s:%d", MQTT_HOST, MQTT_PORT
            )
        except OSError as exc:
            _LOGGER.error("MQTT connect() fehlgeschlagen: %s", exc)
            raise

    # ── MQTT Callbacks (laufen im paho-Hintergrund-Thread) ────────────────────

    def _on_mqtt_connect(self, client, userdata, flags, reason_code, properties=None):
        """
        Callback: MQTT-Verbindung erfolgreich hergestellt.

        Abonniert das Gerätedaten-Topic und sendet eine initiale GET-Anfrage,
        um sofortige Datenlieferung auszulösen.
        """
        # reason_code: 0 (v1) oder ReasonCode.SUCCESS (v2)
        rc = reason_code if isinstance(reason_code, int) else reason_code.value
        if rc == 0:
            self._mqtt_connected = True
            topic = TOPIC_DEVICE_PROPERTY.format(sn=self.serial_number)
            client.subscribe(topic, qos=1)
            _LOGGER.info("MQTT verbunden, abonniere Topic: %s", topic)
            self._send_get_request()
        else:
            _LOGGER.error("MQTT-Verbindung fehlgeschlagen, Code: %s", reason_code)

    def _on_mqtt_disconnect(self, client, userdata, disconnect_flags=None, reason_code=None, properties=None):
        """
        Callback: MQTT-Verbindung unterbrochen.

        paho-mqtt versucht automatisch erneut zu verbinden (loop_start() + reconnect_delay_set).
        """
        self._mqtt_connected = False
        _LOGGER.warning("MQTT-Verbindung getrennt (code=%s), Reconnect läuft…", reason_code)

    def _on_mqtt_message(self, client, userdata, msg):
        """
        Callback: Neue MQTT-Nachricht empfangen.

        Wird im paho-Hintergrund-Thread aufgerufen. Dekodiert die Protobuf-Payload
        und sendet die aktualisierten Daten an den HA-Event-Loop.

        Das Routing zu HA erfolgt thread-sicher über hass.loop.call_soon_threadsafe().
        """
        try:
            battery_packs, energy_stream, ems_heartbeat = decode_mqtt_payload(msg.payload)
        except Exception as exc:
            _LOGGER.debug("Fehler beim Dekodieren der MQTT-Payload: %s", exc)
            return

        if not battery_packs and energy_stream is None and ems_heartbeat is None:
            return

        # Vorhandene Daten aktualisieren (nicht überschreiben)
        with self._mqtt_lock:
            new_batteries = dict(self.data.get(DATA_BATTERIES, {}))
            for pack in battery_packs:
                new_batteries[pack.pack_index] = pack

            new_energy = energy_stream if energy_stream is not None else self.data.get(DATA_ENERGY_STREAM)
            new_ems = ems_heartbeat if ems_heartbeat is not None else self.data.get(DATA_EMS_HEARTBEAT)

            new_data = {
                DATA_BATTERIES: new_batteries,
                DATA_ENERGY_STREAM: new_energy,
                DATA_EMS_HEARTBEAT: new_ems,
            }

        # async_set_updated_data ist ein @callback (keine Koroutine) — muss mit
        # call_soon_threadsafe in den Event-Loop eingeplant werden, nicht mit
        # run_coroutine_threadsafe (das erwartet eine echte Koroutine).
        self.hass.loop.call_soon_threadsafe(
            self.async_set_updated_data, new_data
        )

    # ── GET-Anfrage ───────────────────────────────────────────────────────────

    def _send_get_request(self) -> None:
        """
        Sendet eine GET-Anfrage über MQTT, um sofortige Datenlieferung auszulösen.

        Das Gerät antwortet mit einem vollständigen Statuspaket auf dem
        /app/device/property/{SN} Topic (und ggf. auf get_reply).
        """
        if not self._mqtt_client or not self._mqtt_connected:
            return
        payload = json.dumps({
            "version": "1.0",
            "moduleType": 0,
            "operateType": "get",
            "params": {},
        })
        topic = TOPIC_GET.format(user_id=self._user_id, sn=self.serial_number)
        try:
            self._mqtt_client.publish(topic, payload, qos=1)
            _LOGGER.debug("GET-Anfrage gesendet an %s", topic)
        except Exception as exc:
            _LOGGER.warning("GET-Anfrage fehlgeschlagen: %s", exc)

    # ── DataUpdateCoordinator Interface ───────────────────────────────────────

    async def _async_update_data(self) -> dict[str, Any]:
        """
        Wird periodisch vom DataUpdateCoordinator aufgerufen (Fallback alle 5 Min.).

        Sendet eine GET-Anfrage um das Gerät zu einer sofortigen Datenlieferung zu
        bewegen. Die eigentlichen Daten kommen asynchron über den MQTT-Callback
        und werden direkt per async_set_updated_data() an HA gemeldet.

        Returns:
            Aktueller Datensatz (kann beim ersten Aufruf noch leer sein).

        Raises:
            UpdateFailed: Bei schwerwiegenden Verbindungsfehlern.
        """
        if not self._token:
            try:
                await self.async_setup()
            except Exception as exc:
                raise UpdateFailed(f"Neuverbindung fehlgeschlagen: {exc}") from exc

        # GET-Anfrage senden (löst sofortige Geräteantwort aus)
        if self._mqtt_connected:
            await self.hass.async_add_executor_job(self._send_get_request)

        return self.data

    # ── Aufräumen ─────────────────────────────────────────────────────────────

    async def async_shutdown(self) -> None:
        """
        Trennt die MQTT-Verbindung sauber.

        Wird von async_unload_entry() aufgerufen, wenn die Integration
        deaktiviert oder neu geladen wird.
        """
        if self._mqtt_client:
            _LOGGER.debug("MQTT-Verbindung wird getrennt…")
            await self.hass.async_add_executor_job(self._mqtt_client.loop_stop)
            await self.hass.async_add_executor_job(self._mqtt_client.disconnect)
            self._mqtt_client = None
            self._mqtt_connected = False
