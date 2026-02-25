"""
Konstanten für die EcoFlow PowerOcean Plus Home Assistant Integration.

Dieses Modul definiert alle zentralen Konstanten, die von den verschiedenen
Teilen der Integration verwendet werden — von der Konfiguration über die
MQTT-Kommunikation bis hin zur Protobuf-Dekodierung.
"""

from __future__ import annotations

# ── Integration ──────────────────────────────────────────────────────────────

DOMAIN = "ecoflow_powerocean"
"""Eindeutige Domänen-ID der Integration in Home Assistant."""

MANUFACTURER = "EcoFlow"
MODEL = "PowerOcean Plus"

# ── Konfigurationsschlüssel ───────────────────────────────────────────────────

CONF_SERIAL_NUMBER = "serial_number"
"""Seriennummer des PowerOcean Plus Geräts (z. B. R371ZD1AZH4U0484)."""

CONF_NUM_BATTERY_PACKS = "num_battery_packs"
"""Anzahl der installierten Batterie-Packs (Standard: 2). Bestimmt wie viele
Sensor-Gruppen in Home Assistant angelegt werden. Muss der tatsächlichen
Anzahl der physisch installierten EcoFlow Batterie-Packs entsprechen."""

DEFAULT_NUM_BATTERY_PACKS = 2
"""Standard-Anzahl Batterie-Packs — passend für eine typische 10-kWh-Installation
mit zwei 5-kWh EcoFlow Packs."""

# Hinweis: CONF_EMAIL und CONF_PASSWORD kommen aus homeassistant.const

# ── EcoFlow Cloud API ─────────────────────────────────────────────────────────

API_LOGIN_URL = "https://api.ecoflow.com/auth/login"
"""Endpunkt für den EcoFlow App-Login (Private API). Passwort wird Base64-kodiert übertragen."""

API_CERT_URL = "https://api.ecoflow.com/iot-auth/app/certification"
"""Endpunkt zum Abrufen der MQTT-Zugangsdaten nach erfolgreichem Login."""

API_TIMEOUT = 15
"""Timeout in Sekunden für HTTP-Anfragen an die EcoFlow API."""

# ── MQTT ──────────────────────────────────────────────────────────────────────

MQTT_HOST = "mqtt-e.ecoflow.com"
"""Hostname des EcoFlow Cloud MQTT-Brokers."""

MQTT_PORT = 8883
"""TLS-gesicherter MQTT-Port."""

MQTT_KEEPALIVE = 60
"""Keepalive-Intervall in Sekunden für die MQTT-Verbindung."""

MQTT_RECONNECT_DELAY = 30
"""Wartezeit in Sekunden vor einem erneuten Verbindungsversuch nach Verbindungsabbruch."""

MQTT_FIRST_DATA_TIMEOUT = 20
"""Maximale Wartezeit in Sekunden auf erste Nutzdaten nach dem Verbindungsaufbau."""

TOPIC_DEVICE_PROPERTY = "/app/device/property/{sn}"
"""MQTT-Topic, auf dem das Gerät regelmäßig seinen Zustand veröffentlicht."""

TOPIC_GET = "/app/{user_id}/{sn}/thing/property/get"
"""MQTT-Topic zum aktiven Anfordern von Gerätedaten (GET-Anfrage)."""

TOPIC_GET_REPLY = "/app/{user_id}/{sn}/thing/property/get_reply"
"""MQTT-Topic für die Antwort auf eine GET-Anfrage."""

# ── Protobuf / Nachrichtentypen ───────────────────────────────────────────────

# Alle PowerOcean Plus MQTT-Nachrichten sind Protobuf-kodiert und optional
# XOR-verschlüsselt (enc_type == 1, Schlüssel = seq & 0xFF).

CMD_FUNC_EMS = 96
"""cmdFunc-Wert für alle EMS/PCS/Batterie-Nachrichten."""

CMD_ID_EMS_HEARTBEAT = 1
"""cmdId für JTS1_EMS_HEARTBEAT — Wechselrichter- und Phasendaten."""

CMD_ID_BP_STATUS = 7
"""cmdId für JTS1_BP_STA_REPORT — Einzelstatus je Batterie-Pack."""

CMD_ID_ENERGY_STREAM = 33
"""cmdId für JTS1_ENERGY_STREAM_REPORT — Energiefluss: SOC, Grid, Solar, Last."""

# ── Plattformen ───────────────────────────────────────────────────────────────

PLATFORMS = ["sensor"]
"""Liste der Home Assistant Plattformen, die diese Integration nutzt."""

# ── Datenschlüssel im Coordinator ─────────────────────────────────────────────

DATA_BATTERIES = "batteries"
"""Schlüssel im Coordinator-Datensatz für die Batterie-Pack-Daten (dict: index → BatteryData)."""

DATA_ENERGY_STREAM = "energy_stream"
"""Schlüssel im Coordinator-Datensatz für JTS1_ENERGY_STREAM_REPORT Daten."""

MAX_BATTERY_PACKS = 9
"""Maximale Anzahl unterstützter Batterie-Packs (lt. EcoFlow Spezifikation)."""
