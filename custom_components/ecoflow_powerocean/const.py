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
"""Seriennummer des PowerOcean Plus Geräts (z. B. R37EXAMPLE000001)."""

CONF_NUM_BATTERY_PACKS = "num_battery_packs"
"""Anzahl der installierten Batterie-Packs (Standard: 2). Bestimmt wie viele
Sensor-Gruppen in Home Assistant angelegt werden. Muss der tatsächlichen
Anzahl der physisch installierten EcoFlow Batterie-Packs entsprechen."""

CONF_DEBUG_MODE = "debug_mode"
"""Aktiviert ausführliches Debug-Logging für diese Integration."""

CONF_ENABLE_BACKUP_HELPERS = "enable_backup_helpers"
"""Aktiviert optionale Backup-/Stromausfall-Helfer für zusätzliche Sensoren."""

CONF_BACKUP_RESERVED_SOC_PERCENT = "backup_reserved_soc_percent"
"""SOC-Reserve in Prozent, die für Backup-/Notstrom-Planung unangetastet bleiben soll."""

CONF_POWER_OUTAGE_GRID_POWER_THRESHOLD_W = "power_outage_grid_power_threshold_w"
"""Grenzwert für geringe Netzleistung, unterhalb dessen keine normale Netzversorgung angenommen wird."""

CONF_POWER_OUTAGE_FREQUENCY_MIN_HZ = "power_outage_frequency_min_hz"
"""Mindestfrequenz in Hz, unterhalb der ein Netzausfall als wahrscheinlich gilt."""

CONF_BACKUP_RUNTIME_SMOOTHING_MINUTES = "backup_runtime_smoothing_minutes"
"""Fenster in Minuten, über das der Hausverbrauch für Backup-Schätzungen geglättet wird."""

CONF_BACKUP_CRITICAL_RUNTIME_MINUTES = "backup_critical_runtime_minutes"
"""Schwelle in Minuten, unterhalb der die Restlaufzeit als kritisch markiert wird."""

CONF_ENABLE_BACKUP_OUTAGE_NOTIFICATION = "enable_backup_outage_notification"
"""Aktiviert eine Nachricht, wenn Stromausfall und Batterieversorgung erkannt werden."""

CONF_BACKUP_OUTAGE_NOTIFY_TARGET = "backup_outage_notify_target"
"""Home-Assistant-Notify-Entität für Stromausfall-/Backup-Benachrichtigungen."""

CONF_ENABLE_DAILY_SUNSET_REPORT = "enable_daily_sunset_report"
"""Aktiviert den optionalen Tagesbericht bei Sonnenuntergang."""

CONF_DAILY_REPORT_NOTIFY_TARGET = "daily_report_notify_target"
"""Home-Assistant-Target, an das der Tagesbericht gesendet wird."""

CONF_DAILY_REPORT_FEED_IN_TARIFF_EUR_PER_KWH = (
    "daily_report_feed_in_tariff_eur_per_kwh"
)
"""Einspeisevergütung in Euro pro kWh für den täglichen Komfortbericht."""

DEFAULT_NUM_BATTERY_PACKS = 2
"""Standard-Anzahl Batterie-Packs — passend für eine typische 10-kWh-Installation
mit zwei 5-kWh EcoFlow Packs."""

DEFAULT_DEBUG_MODE = False
"""Debug-Logging standardmäßig deaktiviert."""

DEFAULT_ENABLE_BACKUP_HELPERS = False
"""Backup Helpers sind standardmäßig deaktiviert, damit bestehende Nutzer keine Änderung bemerken."""

DEFAULT_BACKUP_RESERVED_SOC_PERCENT = 10
"""Standardreserve für die Batterie in Prozent."""

DEFAULT_POWER_OUTAGE_GRID_POWER_THRESHOLD_W = 50
"""Standard-Grenzwert für geringe Netzleistung in Watt."""

DEFAULT_POWER_OUTAGE_FREQUENCY_MIN_HZ = 1.0
"""Frequenzen unterhalb dieses Werts gelten für Backup-/Outage-Helfer als ungültig."""

DEFAULT_BACKUP_RUNTIME_SMOOTHING_MINUTES = 10
"""Standardfenster für die Verbrauchsglättung bei Laufzeitschätzungen."""

DEFAULT_BACKUP_CRITICAL_RUNTIME_MINUTES = 120
"""Standardgrenze, ab der die Restlaufzeit als kritisch gilt."""

DEFAULT_ENABLE_BACKUP_OUTAGE_NOTIFICATION = False
"""Stromausfall-Benachrichtigungen sind standardmäßig deaktiviert."""

DEFAULT_BACKUP_OUTAGE_NOTIFY_TARGET = {}
"""Kein Stromausfall-Benachrichtigungsziel voreingestellt."""

DEFAULT_ENABLE_DAILY_SUNSET_REPORT = False
"""Täglicher Sonnenuntergangsbericht ist standardmäßig deaktiviert."""

DEFAULT_DAILY_REPORT_NOTIFY_TARGET = {}
"""Kein Benachrichtigungsziel voreingestellt."""

DEFAULT_DAILY_REPORT_FEED_IN_TARIFF_EUR_PER_KWH = 0.077
"""Effektiver Default aus Westnetz-Beispiel: 20 kWh ergeben 1,54 Euro."""

BACKUP_RESERVED_SOC_PERCENT_MIN = 0
BACKUP_RESERVED_SOC_PERCENT_MAX = 99

POWER_OUTAGE_GRID_POWER_THRESHOLD_W_MIN = 10
POWER_OUTAGE_GRID_POWER_THRESHOLD_W_MAX = 1000

POWER_OUTAGE_FREQUENCY_MIN_HZ_MIN = 0.1
POWER_OUTAGE_FREQUENCY_MIN_HZ_MAX = 49.9

BACKUP_RUNTIME_SMOOTHING_MINUTES_MIN = 1
BACKUP_RUNTIME_SMOOTHING_MINUTES_MAX = 60

BACKUP_CRITICAL_RUNTIME_MINUTES_MIN = 5
BACKUP_CRITICAL_RUNTIME_MINUTES_MAX = 24 * 60

DAILY_REPORT_FEED_IN_TARIFF_MIN = 0.0
DAILY_REPORT_FEED_IN_TARIFF_MAX = 1.0
DAILY_REPORT_FEED_IN_TARIFF_STEP = 0.0001

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

GAP_RECONCILIATION_MIN_SECONDS = 60
"""Mindestdauer einer MQTT-Unterbrechung, ab der eine Lücken-Korrektur berechnet wird."""

GAP_RECONCILIATION_MAX_SECONDS = 6 * 60 * 60
"""Maximale Dauer (in Sekunden), die für eine automatische Lücken-Korrektur berücksichtigt wird.
Längere Unterbrechungen werden aus Sicherheitsgründen nicht automatisch nachgerechnet."""

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

PLATFORMS = ["sensor", "binary_sensor", "button"]
"""Liste der Home Assistant Plattformen, die diese Integration nutzt."""

# ── Datenschlüssel im Coordinator ─────────────────────────────────────────────

DATA_BATTERIES = "batteries"
"""Schlüssel im Coordinator-Datensatz für die Batterie-Pack-Daten (dict: index → BatteryData)."""

DATA_ENERGY_STREAM = "energy_stream"
"""Schlüssel im Coordinator-Datensatz für JTS1_ENERGY_STREAM_REPORT Daten."""

DATA_EMS_HEARTBEAT = "ems_heartbeat"
"""Schlüssel im Coordinator-Datensatz für JTS1_EMS_HEARTBEAT Daten (3-Phasen, MPPT)."""

MAX_BATTERY_PACKS = 9
"""Maximale Anzahl unterstützter Batterie-Packs (lt. EcoFlow Spezifikation)."""
