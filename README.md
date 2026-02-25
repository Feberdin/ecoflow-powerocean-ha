# EcoFlow PowerOcean Plus — Home Assistant Integration

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![HA Version](https://img.shields.io/badge/Home%20Assistant-2024.1%2B-blue.svg)](https://www.home-assistant.io)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Eine inoffizielle Home Assistant Custom Integration für die **EcoFlow PowerOcean Plus** Photovoltaik-Heimspeicheranlage.

> **Status:** Aktiv entwickelt — Batterie-, Energiefluss- und 3-Phasen-Sensoren funktionieren.

---

## Unterstützte Geräte

| Gerät | Seriennummer beginnt mit | Status |
|-------|--------------------------|--------|
| EcoFlow PowerOcean Plus 15 kW | `R37` | ✅ Getestet |
| EcoFlow PowerOcean Plus (andere Varianten) | — | 🔄 Ungetestet |

Bis zu **9 Batterie-Packs** werden automatisch erkannt.

---

## Implementierte Sensoren

### Pro Batterie-Pack (Standard: 2 Packs)

| Sensor | Einheit | HA-Geräteklasse | Standardmäßig aktiv |
|--------|---------|-----------------|---------------------|
| Ladestand (SOC) | % | `battery` | ✅ |
| Gesundheitszustand (SOH) | % | — | ✅ |
| Aktuelle Leistung | W | `power` | ✅ |
| Verbleibende Energie | Wh | `energy_storage` | ✅ |
| Temperatur | °C | `temperature` | ✅ |
| Ladezyklen | — | — | ✅ |
| Spannung | V | `voltage` | ❌ |
| Strom | A | `current` | ❌ |

### Systemweite Sensoren — Energiefluss

| Sensor | Einheit | Beschreibung |
|--------|---------|--------------|
| Solar-Leistung | W | PV-Gesamtertrag (alle MPPT-Strings) |
| Netz-Leistung | W | Positiv = Netzbezug, Negativ = Einspeisung |
| Hausverbrauch | W | Aktuelle Lastleistung |
| Batterie-Gesamtleistung | W | Positiv = Entladen, Negativ = Laden |
| Gesamt-Ladestand | % | Kombinierter SOC aller Batterie-Packs |

### Systemweite Sensoren — Wechselrichter / 3-Phasen

| Sensor | Einheit | Beschreibung |
|--------|---------|--------------|
| Phase L1/L2/L3 Spannung | V | Phasenspannungen des Wechselrichters |
| Phase L1/L2/L3 Strom | A | Phasenströme |
| Phase L1/L2/L3 Leistung | W | Wirkleistung je Phase |
| Netzfrequenz | Hz | Aktuell gemessene Netzfrequenz |
| Batterie-Wechselrichterleistung | W | Batterieleistung auf WR-Seite |
| MPPT 1 Leistung | W | Leistung PV-String 1 |
| MPPT 2 Leistung | W | Leistung PV-String 2 |
| MPPT 3 Leistung | W | Leistung PV-String 3 (standardmäßig deaktiviert) |
| MPPT 4 Leistung | W | Leistung PV-String 4 (standardmäßig deaktiviert) |

> Deaktivierte Sensoren können in HA unter *Einstellungen → Geräte & Dienste → EcoFlow PowerOcean → Entitäten* aktiviert werden.

---

## Energie-Dashboard einrichten

Das Energie-Dashboard benötigt **kumulierte Energiesensoren in kWh** (`device_class: energy`, `state_class: total_increasing`). Die EcoFlow-Sensoren liefern Momentleistung in **Watt** — daher sind zwei Schritte nötig:

1. Template-Sensoren anlegen (Leistung aufteilen, z. B. Netzbezug vs. Einspeisung)
2. Riemann-Integral-Helfer anlegen (Watt → kWh integrieren)

### Schritt 1: Template-Sensoren in `configuration.yaml`

Füge folgenden Block in deine `configuration.yaml` ein. Ersetze alle `[SN]` durch deine Seriennummer in **Kleinbuchstaben** (z. B. `r371zd1azh4u0484`).

```yaml
template:
  - sensor:
      # ── Netz: Bezug (nur positive Werte) ─────────────────────
      - name: "EcoFlow Netzbezug"
        unique_id: ecoflow_grid_import
        unit_of_measurement: "W"
        device_class: power
        state_class: measurement
        state: >
          {{ [states('sensor.ecoflow_powerocean_[SN]_grid_power') | float(0), 0] | max | round(1) }}

      # ── Netz: Einspeisung (nur negative Werte, als positiver Betrag) ──
      - name: "EcoFlow Einspeisung"
        unique_id: ecoflow_grid_export
        unit_of_measurement: "W"
        device_class: power
        state_class: measurement
        state: >
          {{ [states('sensor.ecoflow_powerocean_[SN]_grid_power') | float(0) * -1, 0] | max | round(1) }}

      # ── Batterie: Entladen (nur positive Werte) ───────────────
      - name: "EcoFlow Batterie Entladen"
        unique_id: ecoflow_battery_discharge
        unit_of_measurement: "W"
        device_class: power
        state_class: measurement
        state: >
          {{ [states('sensor.ecoflow_powerocean_[SN]_battery_total_power') | float(0), 0] | max | round(1) }}

      # ── Batterie: Laden (nur negative Werte, als positiver Betrag) ───
      - name: "EcoFlow Batterie Laden"
        unique_id: ecoflow_battery_charge
        unit_of_measurement: "W"
        device_class: power
        state_class: measurement
        state: >
          {{ [states('sensor.ecoflow_powerocean_[SN]_battery_total_power') | float(0) * -1, 0] | max | round(1) }}
```

Nach dem Speichern: Home Assistant neu starten oder *Einstellungen → System → Konfiguration neu laden → Template-Entitäten*.

### Schritt 2: Riemann-Integral-Helfer erstellen

Navigiere zu *Einstellungen → Geräte & Dienste → Helfer → + Helfer erstellen → Integration — Riemann-Summe integral*.

Erstelle für jeden der folgenden Sensoren einen Helfer:

| Helfer-Name | Quell-Sensor | Methode |
|-------------|-------------|---------|
| EcoFlow Solar Energie | `sensor.ecoflow_powerocean_[SN]_solar_power` | Links |
| EcoFlow Netzbezug Energie | `sensor.ecoflow_netzbezug` | Links |
| EcoFlow Einspeisung Energie | `sensor.ecoflow_einspeisung` | Links |
| EcoFlow Batterie Entladen Energie | `sensor.ecoflow_batterie_entladen` | Links |
| EcoFlow Batterie Laden Energie | `sensor.ecoflow_batterie_laden` | Links |

> Einheit wird automatisch auf `kWh` gesetzt wenn der Quell-Sensor `W` und `device_class: power` hat.

### Schritt 3: Energie-Dashboard konfigurieren

Navigiere zu *Einstellungen → Dashboards → Energie* (oder direkt über das Energie-Dashboard → Konfigurieren):

| Dashboard-Bereich | Sensor | Typ |
|-------------------|--------|-----|
| **Netz** → Strom aus dem Netz | `EcoFlow Netzbezug Energie` | kWh |
| **Netz** → Strom zurück ins Netz | `EcoFlow Einspeisung Energie` | kWh |
| **Solar** → Solaranlage hinzufügen | `EcoFlow Solar Energie` | kWh |
| **Heimspeicher** → Energie ins System | `EcoFlow Batterie Entladen Energie` | kWh |
| **Heimspeicher** → Energie aus dem System | `EcoFlow Batterie Laden Energie` | kWh |
| **Heimspeicher** → Aktueller Ladestand | `sensor.ecoflow_powerocean_[SN]_total_soc` | % |

> Der Ladestand (`total_soc`) erscheint direkt ohne Integral-Helfer — er hat bereits `device_class: battery` und `state_class: measurement`.

### Warum Template-Sensoren notwendig sind

Das Energie-Dashboard benötigt für Netz und Batterie **getrennte Sensoren** für Bezug und Einspeisung bzw. Laden und Entladen. Da unsere `grid_power` und `battery_total_power` bidirektional sind (positiv = Bezug/Entladen, negativ = Einspeisung/Laden), teilen die Template-Sensoren den Wert auf.

### Hinweise

- Die Integral-Helfer zählen Energie ab dem Zeitpunkt ihrer Erstellung — historische Werte werden nicht rückwirkend berechnet.
- Nach einem HA-Neustart laufen die Zähler weiter (HA speichert den letzten Wert).
- Schwankt `grid_power` leicht um 0 W (±5 W), erscheinen minimale Werte bei Bezug **und** Einspeisung gleichzeitig — das ist normal und beeinflusst die Monatssummen kaum.

---

## Geplante Erweiterungen

- [ ] Lokaler Modbus TCP Zugriff (ohne Cloud)
- [ ] Automatische Template-Sensoren via Integration (kein manuelles YAML)

---

## Technischer Hintergrund

### Kommunikationsprotokoll

Die EcoFlow PowerOcean Plus kommuniziert ausschließlich über die **EcoFlow Cloud** — es gibt keine öffentlich dokumentierte lokale API. Diese Integration nutzt den gleichen Kommunikationsweg wie die offizielle EcoFlow App:

```
Home Assistant
    │
    ├─ HTTPS ──► api.ecoflow.com          (Login + MQTT-Credentials)
    │
    └─ MQTTS ──► mqtt-e.ecoflow.com:8883  (Echtzeit-Gerätedaten)
                      │
                      └── PowerOcean Plus verbindet sich ebenfalls hier
```

### Protobuf-Kodierung

Alle MQTT-Nachrichten sind im [Protocol Buffers](https://protobuf.dev/) Format kodiert und zusätzlich XOR-verschlüsselt:

```
MQTT Payload
└── HeaderMessage (Protobuf)
    └── repeated Header
        ├── cmd_func + cmd_id  → Bestimmt den Nachrichtentyp
        ├── enc_type == 1      → XOR-Verschlüsselung aktiv
        ├── seq                → XOR-Schlüssel (niedrigstes Byte)
        └── pdata              → Innere Nutzdaten (weiteres Protobuf)
```

Relevante Nachrichtentypen:

| cmdFunc | cmdId | Typ | Inhalt |
|---------|-------|-----|--------|
| 96 | 7 | `JTS1_BP_STA_REPORT` | Batterie-Pack-Status |
| 96 | 33 | `JTS1_ENERGY_STREAM_REPORT` | Energiefluss-Übersicht |
| 96 | 1 | `JTS1_EMS_HEARTBEAT` | Wechselrichter / 3-Phasen |

### Warum nicht die offizielle EcoFlow Developer API?

EcoFlow bietet eine [Developer API](https://developer.ecoflow.com) mit Developer API Keys an. Diese API gibt jedoch für den PowerOcean Plus den Fehler **1006 "not allowed to get device info"** zurück — der PowerOcean Plus wird über den normalen REST-Endpunkt nicht unterstützt. Auch das MQTT-Topic der Open API liefert keine Daten für dieses Gerät.

Diese Integration verwendet daher die **Private API** (App-Login), die dieselbe Grundlage wie die offizielle EcoFlow App nutzt.

---

## Installation

### Voraussetzungen

- Home Assistant 2024.1 oder neuer
- EcoFlow-Benutzerkonto (App-Login, **nicht** Developer API Keys)
- Seriennummer des PowerOcean Plus (auf dem Typenschild oder in der EcoFlow App)
- Aktive Internetverbindung des Home Assistant Servers

### Methode 1: HACS (empfohlen)

1. HACS öffnen → *Integrationen* → ⋮ Menü → *Benutzerdefinierte Repositories*
2. Repository-URL eintragen: `https://github.com/Feberdin/ecoflow-powerocean-ha`
3. Kategorie: *Integration*
4. *EcoFlow PowerOcean* in HACS suchen und installieren
5. Home Assistant neu starten

### Methode 2: Manuelle Installation

1. Dieses Repository herunterladen
2. Den Ordner `custom_components/ecoflow_powerocean/` in das Verzeichnis
   `<config>/custom_components/` deines Home Assistant kopieren
3. Home Assistant neu starten

### Integration einrichten

1. *Einstellungen → Geräte & Dienste → + Integration hinzufügen*
2. Nach "EcoFlow PowerOcean" suchen
3. Formular ausfüllen:
   - **E-Mail:** EcoFlow App-Konto E-Mail
   - **Passwort:** EcoFlow App-Konto Passwort
   - **Seriennummer:** z. B. `R371ZD1AZH4U0484`
4. *Absenden* — die Integration prüft die Zugangsdaten sofort

---

## Fehlerbehebung

### Sensor zeigt "Unavailable"

- Prüfe ob das Gerät online ist (EcoFlow App öffnen)
- Prüfe die Home Assistant Netzwerkverbindung
- Überprüfe die Logs: *Einstellungen → System → Logs → EcoFlow*

### Login schlägt fehl

- Stelle sicher, dass du **App-Zugangsdaten** verwendest (nicht Developer API Keys)
- Das Passwort darf Sonderzeichen enthalten — diese werden korrekt behandelt
- Bei Zwei-Faktor-Authentifizierung: diese muss in der EcoFlow App deaktiviert sein

### Debug-Logging aktivieren

Füge in `configuration.yaml` hinzu:

```yaml
logger:
  default: warning
  logs:
    custom_components.ecoflow_powerocean: debug
```

---

## Mitwirken

Beiträge sind willkommen! Bitte lies [CONTRIBUTING.md](CONTRIBUTING.md) für Details.

**Besonders gesucht:**
- Tester mit anderen PowerOcean Plus Varianten
- Entwickler für die Modbus TCP lokale Integration
- Übersetzer für weitere Sprachen

---

## Danksagungen

Diese Integration basiert auf der Arbeit folgender Open-Source-Projekte:

- [foxthefox/ioBroker.ecoflow-mqtt](https://github.com/foxthefox/ioBroker.ecoflow-mqtt) — Protobuf-Schema und Protokoll-Dokumentation
- [tolwi/hassio-ecoflow-cloud](https://github.com/tolwi/hassio-ecoflow-cloud) — API-Struktur und HA-Integrationsmuster
- [mmiller7/ecoflow-withoutflow](https://github.com/mmiller7/ecoflow-withoutflow) — MQTT-Credential-Extraktion

---

## Lizenz

MIT — siehe [LICENSE](LICENSE)

---

## Haftungsausschluss

Diese Integration ist **nicht offiziell von EcoFlow unterstützt oder autorisiert**.
EcoFlow kann die API jederzeit ändern, was zu Ausfällen der Integration führen kann.
Die Nutzung erfolgt auf eigene Gefahr.
