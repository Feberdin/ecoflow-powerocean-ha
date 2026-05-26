# EcoFlow PowerOcean Plus — Home Assistant Integration

[![Version](https://img.shields.io/github/v/release/Feberdin/ecoflow-powerocean-ha?label=Version&color=blue)](https://github.com/Feberdin/ecoflow-powerocean-ha/releases/latest)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![HA Version](https://img.shields.io/badge/Home%20Assistant-2024.1%2B-blue.svg)](https://www.home-assistant.io)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Inoffizielle Home Assistant Integration für die **EcoFlow PowerOcean Plus** Photovoltaik-Heimspeicheranlage. Echtzeit-Monitoring via MQTT — Batterie, Solar, Netz, 3-Phasen und Energie-Dashboard direkt out of the box.

---

## Highlights

- **Batterie-Monitoring** — SOC, SOH, Temperatur, Zyklen, Leistung für bis zu 9 Packs
- **Energiefluss** — Solar, Netz, Hausverbrauch, Batterie-Gesamtleistung
- **3-Phasen Wechselrichter** — Spannung, Strom, Wirk-/Blind-/Scheinleistung je Phase
- **MPPT-Strings** — Leistung, Spannung, Strom für bis zu 4 Strings
- **Energie-Dashboard** — kWh-Zähler direkt integriert, kein YAML nötig
- **Verbindungsstatus** — MQTT-Verbindung als Sensor für Automationen
- **Options Flow** — Anzahl Batterie-Packs jederzeit änderbar ohne Neueinrichtung
- **Gap-Reconciliation** — bei kurzer Internet-Unterbrechung wird die Energielücke beim Reconnect transparent geschätzt
- **Backup Helpers (optional)** — Laufzeitabschätzung, Stromausfall-Erkennung und Hilfszustände für eigene Automationen
- **Täglicher Sonnenuntergangsbericht (optional)** — Einspeise-kWh, geschätzte Vergütung und Akku-100%-Dauer per Home-Assistant-Nachricht

---

## Feature-Wünsche & Feedback

Wenn du neue Funktionen vorschlagen möchtest, nutze bitte das Feature-Template:

- [Feature-Wunsch erstellen](https://github.com/Feberdin/ecoflow-powerocean-ha/issues/new?template=feature_request.md&title=%5BFEATURE%5D%20)

Für Fehler bitte das Bug-Template verwenden:

- [Bug melden](https://github.com/Feberdin/ecoflow-powerocean-ha/issues/new?template=bug_report.md&title=%5BBUG%5D%20)

So bleiben Anforderungen und Prioritäten transparent, und wir können Änderungen besser planen.

---

## Screenshots

### Geräte- und Sensorübersicht in Home Assistant

![Geräteübersicht EcoFlow PowerOcean Plus](docs/images/ha-device-overview.png)

![Detailansicht der Entitäten und Messwerte](docs/images/ha-entity-list-detail.png)

### Energie-Dashboard (Werte aus dieser Integration)

![Energie-Zusammenfassung mit Verteilung, Quellen und Zeitreihen](docs/images/ha-energy-summary.png)

![Wochenansicht mit Stromnutzung, PV-Erzeugung und Kennzahlen](docs/images/ha-energy-dashboard-weekly.png)

### Beispielvisualisierung mit Lumina Energy Card

![Lumina Energy Card Beispielansicht](docs/images/lumina-energy-card-example.png)

> Hinweis: Die **Lumina Energy Card** ist eine separate Dashboard-Karte.  
> Diese Integration liefert die Sensorwerte, die Visualisierung selbst stammt von Lumina.

---

## Unterstützte Geräte

| Gerät | Seriennummer | Status |
|-------|-------------|--------|
| EcoFlow PowerOcean Plus 15 kW | beginnt mit `R37` | ✅ Getestet |
| EcoFlow PowerOcean Plus (andere Varianten) | — | 🔄 Ungetestet, Feedback willkommen |

---

## Installation

### Methode 1: HACS (empfohlen)

1. HACS öffnen → *Integrationen* → ⋮ → *Benutzerdefinierte Repositories*
2. URL eintragen: `https://github.com/Feberdin/ecoflow-powerocean-ha`, Kategorie: *Integration*
3. *EcoFlow PowerOcean* installieren → Home Assistant neu starten

### Methode 2: Manuell

1. [`custom_components/ecoflow_powerocean/`](custom_components/ecoflow_powerocean/) herunterladen
2. In `<config>/custom_components/ecoflow_powerocean/` kopieren
3. Home Assistant neu starten

### Integration einrichten

*Einstellungen → Geräte & Dienste → + Integration hinzufügen → „EcoFlow PowerOcean"*

| Feld | Beschreibung |
|------|-------------|
| **E-Mail** | EcoFlow App-Konto (nicht Developer API Keys) |
| **Passwort** | EcoFlow App-Passwort (Sonderzeichen werden korrekt verarbeitet) |
| **Seriennummer** | z. B. `R37EXAMPLE000001` — auf dem Typenschild oder in der App |
| **Batterie-Packs** | Anzahl installierter Packs (Standard: 2) |

> **Hinweis:** Zwei-Faktor-Authentifizierung muss in der EcoFlow App deaktiviert sein.

### Anzahl Batterie-Packs nachträglich ändern

*Einstellungen → Geräte & Dienste → EcoFlow PowerOcean → Konfigurieren*

Die Integration lädt sich danach automatisch neu.

### Backup Helpers aktivieren

Die Backup Helpers sind **optional** und standardmäßig **deaktiviert**.
Du findest sie ebenfalls unter:

*Einstellungen → Geräte & Dienste → EcoFlow PowerOcean → Konfigurieren*

Damit bleibt die Kernintegration für alle bestehenden Nutzer unverändert. Erst wenn du das Feature aktivierst, werden zusätzliche Helper-Entitäten angelegt.

---

## Täglicher Sonnenuntergangsbericht

Der tägliche Sonnenuntergangsbericht ist **optional** und standardmäßig **deaktiviert**.
Du aktivierst ihn unter:

*Einstellungen → Geräte & Dienste → EcoFlow PowerOcean → Konfigurieren*

Der Bericht sendet bei Sonnenuntergang eine Home-Assistant-Nachricht an das gewählte Ziel.
Er enthält:

- die an diesem lokalen Kalendertag bis Sonnenuntergang eingespeiste Energie in kWh
- die daraus berechnete Vergütung in Euro
- die Dauer, in der der Akku an diesem Tag bei 100 % SOC stand

### Optionen

| Option | Standard | Bedeutung |
|--------|----------|-----------|
| `Täglichen Sonnenuntergangsbericht aktivieren` | `false` | Schaltet die tägliche Nachricht frei |
| `Benachrichtigungsziel` | leer | Home-Assistant-Ziel für `notify.send_message` |
| `Einspeisevergütung (€/kWh)` | `0,077` | Tarif für `Einspeisung kWh × Tarif` |

Der Default-Tarif `0,077 €/kWh` stammt aus einer Westnetz-Abrechnung:
17 kWh zu `0,0786 €/kWh` und 3 kWh zu `0,068 €/kWh` ergeben zusammen 20 kWh und 1,54 Euro.

Die Werte sind Monitoring- und Komfortwerte der Integration. Sie sind nicht als rechtsverbindliche Abrechnung gedacht, weil MQTT-/HA-Ausfälle defensiv begrenzt und nicht abrechnungsgenau rekonstruiert werden.

---

## Backup Helpers

Der Backup-Helper-Layer bewertet den aktuellen Backup-Zustand deiner Anlage, ohne direkte Fremdsteuerung in den Core einzubauen.

Wichtig:
- Die Integration **erkennt und bewertet** Backup-/Outage-Zustände.
- Die eigentliche Aktion baust du selbst als Home-Assistant-Automation.
- Es gibt **keine feste Unraid-, Tuya- oder Steckdosen-Logik** im Python-Code.

### Optionen

| Option | Standard | Bedeutung |
|--------|----------|-----------|
| `Backup Helpers aktivieren` | `false` | Schaltet die zusätzlichen Helper-Entitäten frei |
| `Reservierter Backup-SOC (%)` | `10` | Prozentuale Batterie-Reserve, die für Laufzeit-Schätzungen nicht verplant wird |
| `Grenzwert Netzleistung für Ausfallerkennung (W)` | `50` | Netzleistung innerhalb dieses Bereichs zählt als „nahe null“ |
| `Mindest-Netzfrequenz für gültiges Netzsignal (Hz)` | `1.0` | Frequenzen darunter oder fehlende Frequenz nach zuvor gültigem Signal gelten als Hinweis auf Netzausfall |
| `Glättungsfenster für Laufzeit (Minuten)` | `10` | Mittelt den Hausverbrauch, damit Peaks die Laufzeit nicht zu stark verzerren |
| `Kritische Restlaufzeit (Minuten)` | `120` | Unterhalb dieses Werts wird die Backup-Reserve als kritisch markiert |

### Neue Sensoren

| Sensor | Einheit | Bedeutung |
|--------|---------|-----------|
| `Geschätzte Backup-Laufzeit (Minuten)` | min | Restlaufzeit auf Basis geglätteter Last und nutzbarer Energie |
| `Geschätzte Backup-Laufzeit (Stunden)` | h | Dieselbe Information in Stunden |
| `Nutzbare Backup-Energie` | Wh | Energie oberhalb der konfigurierten SOC-Reserve |
| `Empfohlene Backup-Aktion` | Enum | `normal` / `shed_load` / `shutdown_recommended` / `unknown` |

### Neue Binary-Sensoren

| Binary Sensor | Bedeutung |
|---------------|-----------|
| `Stromausfall erkannt` | Netzverlust ist nach kombinierter Heuristik wahrscheinlich |
| `Backup-Reserve kritisch` | Geschätzte Restlaufzeit liegt unter deiner kritischen Schwelle |
| `Backup aktiv` | Das Haus wird im erkannten Backup-/Inselzustand plausibel lokal versorgt |

### Wie die Stromausfall-Erkennung arbeitet

Die Erkennung ist bewusst **konservativ** und vermeidet Fehlalarme im normalen Nullpunktbetrieb.

Ein Stromausfall wird nur dann als wahrscheinlich gewertet, wenn über einen kurzen stabilen Zeitraum gleichzeitig gilt:
- Es gab zuvor gültige Netzfrequenz-Samples, und die Frequenz fehlt jetzt oder liegt unter dem konfigurierten Mindestwert
- Die Netzleistung bleibt nahe `0 W`
- Es liegt echte Hauslast an
- PV und/oder Batterie versorgen das Haus plausibel weiter

Wenn die Anlage **nie ein brauchbares Frequenzsignal liefert**, bleibt `Stromausfall erkannt` absichtlich aus. In diesem Fall sind die Laufzeit- und Reserve-Sensoren trotzdem nutzbar, aber die Outage-Erkennung ist bewusst zurückhaltend.

### Automationsbeispiele

Die folgenden Beispiele sind **nur Dokumentation**. Du passt die Ziel-Entitäten an deine eigene Home-Assistant-Umgebung an.

#### 1. Bei Stromausfall Unraid sauber herunterfahren

```yaml
alias: PowerOcean Backup - Unraid sauber herunterfahren
mode: single
trigger:
  - platform: state
    entity_id: binary_sensor.mein_powerocean_stromausfall
    to: "on"
    for: "00:01:00"
condition:
  - condition: state
    entity_id: binary_sensor.unraid_server_online
    state: "on"
action:
  - service: button.press
    target:
      entity_id: button.unraid_graceful_shutdown
```

#### 2. Bei kritischer Restlaufzeit bestimmte Steckdosen ausschalten

```yaml
alias: PowerOcean Backup - Nicht kritische Lasten abschalten
mode: single
trigger:
  - platform: state
    entity_id: binary_sensor.mein_powerocean_backup_reserve_kritisch
    to: "on"
    for: "00:02:00"
condition:
  - condition: state
    entity_id: binary_sensor.mein_powerocean_backup_aktiv
    state: "on"
action:
  - service: switch.turn_off
    target:
      entity_id:
        - switch.waschmaschine
        - switch.trockner
        - switch.garagensteckdose
```

Diese Beispiele zeigen den gewünschten Architekturpunkt:
- **Die Integration liefert Hilfs-Entitäten**
- **Home Assistant entscheidet per Automation, was konkret passieren soll**

---

## Sensoren

### Pro Batterie-Pack

| Sensor | Einheit | Aktiv |
|--------|---------|:-----:|
| Ladestand (SOC) | % | ✅ |
| Gesundheitszustand (SOH) | % | ✅ |
| Leistung | W | ✅ |
| Verbleibende Energie | Wh | ✅ |
| Temperatur (Umgebung) | °C | ✅ |
| Ladezyklen | — | ✅ |
| MOSFET-Temperatur | °C | ❌ |
| Spannung | V | ❌ |
| Strom | A | ❌ |

### System — Energiefluss

| Sensor | Einheit | Beschreibung | Aktiv |
|--------|---------|-------------|:-----:|
| Solar-Leistung | W | PV-Gesamtertrag aller MPPT-Strings | ✅ |
| Netz-Leistung | W | Positiv = Bezug, Negativ = Einspeisung | ✅ |
| Hausverbrauch | W | Aktuelle Lastleistung | ✅ |
| Batterie-Gesamtleistung | W | Positiv = Entladen, Negativ = Laden | ✅ |
| Gesamt-Ladestand | % | Kombinierter SOC aller Packs | ✅ |
| Batterie-Gesamtenergie | Wh | Verbleibende Energie systemweit | ✅ |
| Aktive Batterie-Module | — | Anzahl kommunizierender Packs | ✅ |
| DC-Bus-Spannung | V | Interne DC-Bus-Spannung | ❌ |

### System — Wechselrichter / 3-Phasen

| Sensor | Einheit | Aktiv |
|--------|---------|:-----:|
| Phase L1/L2/L3 Spannung | V | ✅ |
| Phase L1/L2/L3 Strom | A | ✅ |
| Phase L1/L2/L3 Wirkleistung | W | ✅ |
| Phase L1/L2/L3 Blindleistung | var | ❌ |
| Phase L1/L2/L3 Scheinleistung | VA | ❌ |
| Netzfrequenz | Hz | ✅ |
| MPPT 1/2 Leistung | W | ✅ |
| MPPT 3/4 Leistung | W | ❌ |
| MPPT 1–4 Spannung | V | ❌ |
| MPPT 1–4 Strom | A | ❌ |

### Energie-Akkumulatoren (für Energie-Dashboard)

| Sensor | Einheit | Beschreibung |
|--------|---------|-------------|
| Solar-Energie | kWh | Kumulierter PV-Ertrag |
| Netz-Bezug | kWh | Kumulierter Strombezug |
| Netz-Einspeisung | kWh | Kumulierte Einspeisung |
| Batterie-Entnahme | kWh | Kumulierte Energie aus der Batterie |
| Batterie-Ladung | kWh | Kumulierte Energie in die Batterie |

### Status

| Sensor | Beschreibung |
|--------|-------------|
| Verbindungsstatus | MQTT-Verbindung: `connected` / `disconnected` |

> Deaktivierte Sensoren lassen sich unter *Einstellungen → Geräte & Dienste → EcoFlow PowerOcean → Entitäten* aktivieren.

---

## Energie-Dashboard einrichten

Die kWh-Sensoren sind direkt einsatzbereit. Navigiere zu *Einstellungen → Dashboards → Energie*:

| Dashboard-Bereich | Sensor |
|-------------------|--------|
| **Netz** → Strom aus dem Netz | `Netz-Bezug` |
| **Netz** → Strom zurück ins Netz | `Netz-Einspeisung` |
| **Solar** → Solaranlage | `Solar-Energie` |
| **Heimspeicher** → Energie ins System | `Batterie-Entnahme` |
| **Heimspeicher** → Energie aus dem System | `Batterie-Ladung` |
| **Heimspeicher** → Aktueller Ladestand | `Gesamt-Ladestand` |

**Hinweise:**
- Zähler starten mit der ersten MQTT-Nachricht — historische Werte werden nicht rückwirkend berechnet
- Werte bleiben über HA-Neustarts erhalten
- Bei MQTT-/Internet-Lücken wird beim Reconnect eine Schätzung angewendet
  (Trapezregel aus letzter Leistung vor Disconnect und erster Leistung nach Reconnect)
- Sehr lange Unterbrechungen werden aus Sicherheitsgründen nicht automatisch nachgerechnet
- Kleine Messschwankungen (±5 W) können gleichzeitig minimale Bezugs- und Einspeisungswerte erzeugen — physikalisch normal, Einfluss auf Monatssummen vernachlässigbar

---

## Fehlerbehebung

### Sensor zeigt „Nicht verfügbar"

1. EcoFlow App öffnen — ist das Gerät dort online?
2. HA-Netzwerkverbindung prüfen
3. Logs prüfen: *Einstellungen → System → Logs → „ecoflow"*
4. Verbindungsstatus-Sensor prüfen: zeigt er `disconnected`?
5. Im Verbindungsstatus-Sensor die Attribute `last_gap_*` prüfen (Start/Ende/Dauer der letzten Lücke)

### Login schlägt fehl

- **App-Zugangsdaten** verwenden (E-Mail + Passwort der EcoFlow App, keine Developer API Keys)
- Bei 2FA: muss in der EcoFlow App deaktiviert sein
- Sonderzeichen im Passwort werden korrekt behandelt

### HACS zeigt kein Update an oder „Konfigurieren“ lädt nicht

1. In Home Assistant prüfen, welche Version wirklich installiert ist:

   ```bash
   grep '"version"' /config/custom_components/ecoflow_powerocean/manifest.json
   ```

2. Wenn dort nicht die aktuelle GitHub-Release-Version steht: HACS öffnen,
   *EcoFlow PowerOcean* auswählen und über das Menü die Informationen neu laden
   oder die Integration erneut herunterladen.
3. Danach Home Assistant vollständig neu starten, nicht nur die Integration neu laden.
4. Wenn der Konfigurieren-Dialog weiter mit `400: Bad Request` abbricht, diese
   Logger kurzzeitig aktivieren und den neuen Log-Ausschnitt anhängen:

   ```yaml
   logger:
     default: warning
     logs:
       homeassistant.config_entries: debug
       homeassistant.helpers.data_entry_flow: debug
       custom_components.ecoflow_powerocean: debug
       custom_components.ecoflow_powerocean.config_flow: debug
   ```

### Debug-Logging aktivieren

**Einfach über die UI (empfohlen):**

1. *Einstellungen → Geräte & Dienste → EcoFlow PowerOcean → Konfigurieren*
2. Option **„Debug-Modus aktivieren“** einschalten
3. Speichern (Integration wird neu geladen)

**Diagnose-Datei für Support/Issues exportieren:**

1. *Einstellungen → Geräte & Dienste → EcoFlow PowerOcean*
2. Menü (⋮) → **„Diagnose herunterladen“**
3. Die heruntergeladene Datei im GitHub-Issue anhängen

Die Diagnose redigiert sensible Daten (z. B. Passwort/Token/Seriennummer) automatisch.

**Alternativ per YAML:**

```yaml
# configuration.yaml
logger:
  default: warning
  logs:
    custom_components.ecoflow_powerocean: debug
```

---

## Technischer Hintergrund

### Kommunikation

Die PowerOcean Plus kommuniziert ausschließlich über die EcoFlow Cloud — eine lokale API ist nicht öffentlich dokumentiert. Diese Integration nutzt denselben Weg wie die offizielle EcoFlow App:

```
Home Assistant
    ├─ HTTPS ──► api.ecoflow.com          (Login + MQTT-Credentials)
    └─ MQTTS ──► mqtt-e.ecoflow.com:8883  (Echtzeit-Gerätedaten)
```

### Protokoll

Alle MQTT-Nachrichten sind als [Protocol Buffers](https://protobuf.dev/) kodiert und XOR-verschlüsselt. Der Decoder ist in reinem Python implementiert — keine nativen Abhängigkeiten außer `paho-mqtt`.

| cmdFunc | cmdId | Nachrichtentyp | Inhalt |
|---------|-------|---------------|--------|
| 96 | 1 | `JTS1_EMS_HEARTBEAT` | Wechselrichter, 3-Phasen, MPPT |
| 96 | 7 | `JTS1_BP_STA_REPORT` | Batterie-Pack-Status |

### Warum nicht die offizielle Developer API?

Die EcoFlow Developer API gibt für den PowerOcean Plus den Fehler **1006 „not allowed"** zurück. Das MQTT-Topic der Open API liefert ebenfalls keine Daten. Diese Integration verwendet daher die Private API (App-Login) — identisch mit der offiziellen EcoFlow App.

---

## Mitwirken

Beiträge, Bugreports und Feedback sind herzlich willkommen!

### Lokale Validierung

```bash
cd /Users/joachim.stiegler/EcoFlow/ha_integration
python3 -m unittest tests.test_backup_helpers
python3 -m py_compile custom_components/ecoflow_powerocean/*.py
```

**Besonders gesucht:**
- Tester mit anderen PowerOcean Plus Varianten (andere Leistungsklassen, andere Seriennummern)
- Entwickler für lokalen Modbus TCP Zugriff (Port 502 ist offen)

Issues und Pull Requests bitte über GitHub einreichen.

---

## Danksagungen

- [foxthefox/ioBroker.ecoflow-mqtt](https://github.com/foxthefox/ioBroker.ecoflow-mqtt) — Protobuf-Schema und Protokolldokumentation
- [tolwi/hassio-ecoflow-cloud](https://github.com/tolwi/hassio-ecoflow-cloud) — API-Struktur und HA-Integrationsmuster
- [mmiller7/ecoflow-withoutflow](https://github.com/mmiller7/ecoflow-withoutflow) — MQTT-Credential-Extraktion

---

## Versionslog

> Die Reihenfolge ist chronologisch nach inhaltlicher Entwicklung.  
> GitHub-Release-Publikationszeiten können davon abweichen.

| Version | Inhalt | Beweggrund |
|---------|--------|------------|
| `v0.1.2` | Basis der Sensor-Entitäten stabilisiert (Battery-Sensoren beim Setup vorbereitet) | Zuverlässigere Entitätserstellung beim ersten Laden |
| `v0.1.3` | MQTT-Auth-Probleme (`Not authorized`) behoben | Verbindungsaufbau zum Broker robuster machen |
| `v0.1.4` | MQTT Client-ID Format korrigiert | Kompatibilität mit EcoFlow Brokeranforderungen |
| `v0.2.0` | Breitere Sensorabdeckung aus API/MQTT-Daten | Mehr Messwerte für reale PV-Setups verfügbar machen |
| `v0.2.1` | Energiefluss-Sensoren korrigiert | Falsche/inkonsistente Livewerte reduzieren |
| `v0.2.2` | Energie-Dashboard ohne zusätzliche YAML-Konfiguration nutzbar | Einstieg für Nutzer ohne manuelle YAML-Arbeit vereinfachen |
| `v0.2.3` | Netzfrequenz-Fix | Stabilere Hz-Anzeige trotz lückenhafter Telegramme |
| `v0.3.0` | Neue Sensoren, Options Flow, Verbindungsstatus | Bedienbarkeit erhöhen und Konfiguration über UI ermöglichen |
| `v0.3.1` | Vorzeichen-/Leistungslogik verbessert | Abweichungen zwischen App und HA bei Grid/Battery reduzieren |
| `v0.3.2` | Weitere Korrekturen im Energiefluss | Konsistentere Bilanz bei wechselnden Lastsituationen |
| `v0.3.3` | Stabilitäts- und Datenqualitätsfixes | Zuverlässigkeit im Dauerbetrieb erhöhen |
| `v0.3.4` | Debug-Modus + Diagnostics-Export | Support und Fehleranalyse für Nutzer/Issues vereinfachen |
| `v0.3.5` | Fix für `TypeError` nach Debug-Umschaltung (`num_battery_packs` float/int) | Absturz beim Reconfigure zuverlässig beheben |
| `v0.3.6` | Gap-Reconciliation bei MQTT/Internet-Lücken (geschätzte Nachführung) + Gap-Metadaten | Energie-Summen nach Verbindungsabbrüchen nachvollziehbar weiterführen |
| `v0.4.0` | Optionaler Backup-Helper-Layer mit Laufzeitabschätzung, Stromausfall-Heuristik und Binary-Sensoren | Backup-/Outage-Zustände bewerten, ohne Fremdsteuerung hart in den Core zu bauen |
| `v0.4.1` | Optionaler täglicher Sonnenuntergangsbericht mit Einspeise-kWh, Vergütung und Akku-100%-Dauer | Komfortauswertung für Tagesertrag und volle Akku-Zeit direkt per HA-Nachricht |
| `v0.4.2` | Options-Flow-Fix für Benachrichtigungsziel-Auswahl | Konfigurieren-Dialog in Home Assistant wieder zuverlässig laden |
| `v0.4.3` | Notify-Ziel im Options Flow auf robustes Textfeld umgestellt | 400-Fehler beim Laden des Konfigurieren-Dialogs vermeiden |
| `v0.4.4` | Kompatibilitätsfix für `ConfigFlowResult` in Home Assistant 2024.1 | Config-Flow-Import auf älteren HA-Versionen wieder ermöglichen |
| `v0.4.5` | Options Flow initialisiert den zugehörigen Config Entry explizit | Konfigurieren-Dialog auf HA 2024.1 kompatibel starten |
| `v0.4.6` | HACS-/HA-Troubleshooting für installierte Manifest-Version ergänzt | Sichtbar machen, ob Home Assistant wirklich den aktuellen Code geladen hat |
| `v0.4.7` | Options Flow nutzt eigenen Config-Entry-Verweis statt HA-Property zu überschreiben | 500-Fehler beim Konfigurieren auf neueren HA-Versionen vermeiden |

---

## Lizenz

MIT — siehe [LICENSE](LICENSE)

**Haftungsausschluss:** Diese Integration ist nicht offiziell von EcoFlow unterstützt oder autorisiert. EcoFlow kann die API jederzeit ändern. Nutzung auf eigene Gefahr.
