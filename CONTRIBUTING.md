# Beitragen zur EcoFlow PowerOcean Plus HA Integration

Vielen Dank für dein Interesse, zu diesem Projekt beizutragen! Beiträge aller Art sind willkommen — egal ob Bugfixes, neue Features, Dokumentationsverbesserungen oder das Testen mit anderen Gerätevarianten.

---

## Inhaltsverzeichnis

- [Verhaltenskodex](#verhaltenskodex)
- [Wie kann ich beitragen?](#wie-kann-ich-beitragen)
- [Bugs melden](#bugs-melden)
- [Feature-Anfragen](#feature-anfragen)
- [Code beitragen](#code-beitragen)
- [Entwicklungsumgebung einrichten](#entwicklungsumgebung-einrichten)
- [Coding-Standards](#coding-standards)
- [Pull Request Prozess](#pull-request-prozess)
- [Besonders gesucht](#besonders-gesucht)

---

## Verhaltenskodex

Dieses Projekt folgt unserem [Verhaltenskodex](CODE_OF_CONDUCT.md). Durch deine Teilnahme verpflichtest du dich, diesen einzuhalten.

---

## Wie kann ich beitragen?

### Bugs melden

Bevor du einen Bug meldest:

1. Überprüfe die [vorhandenen Issues](https://github.com/Feberdin/ecoflow-powerocean-ha/issues), ob der Bug bereits gemeldet wurde
2. Aktiviere Debug-Logging und prüfe die Logs:
   ```yaml
   # configuration.yaml
   logger:
     default: warning
     logs:
       custom_components.ecoflow_powerocean: debug
   ```
3. Notiere die exakte Fehlermeldung und den Kontext

Erstelle dann ein neues Issue mit der **Bug-Report-Vorlage**.

### Feature-Anfragen

Feature-Anfragen sind willkommen! Bitte:

1. Überprüfe, ob das Feature bereits in einem Issue diskutiert wird
2. Erkläre den Use Case und warum das Feature nützlich wäre
3. Erstelle ein Issue mit der **Feature-Request-Vorlage**

### Geräte testen

Besonders wertvoll ist das Testen mit anderen EcoFlow-Geräten. Wenn du ein anderes Gerät als den PowerOcean Plus 15 kW (R37-Seriennummer) hast:

1. Installiere die Integration und teste sie
2. Melde Ergebnisse (funktioniert / funktioniert nicht / teilweise) als Issue
3. Teile anonymisierte MQTT-Rohdaten wenn möglich

---

## Code beitragen

### Entwicklungsumgebung einrichten

**Voraussetzungen:**
- Python 3.9+
- Home Assistant (Entwicklungsinstallation oder echter HA-Server)
- `paho-mqtt >= 1.6.1`

**Repository klonen:**
```bash
git clone https://github.com/Feberdin/ecoflow-powerocean-ha.git
cd ecoflow-powerocean-ha
```

**Integration in HA einbinden (Entwicklungsmodus):**
```bash
# Symlink in dein HA custom_components Verzeichnis
ln -s $(pwd)/custom_components/ecoflow_powerocean \
    /path/to/homeassistant/custom_components/ecoflow_powerocean
```

**Test-Skripte nutzen:**
```bash
# MQTT-Verbindung testen (EcoFlow-Zugangsdaten erforderlich)
python test_mqtt.py

# Protobuf-Decoder testen
python decode_mqtt.py
```

---

## Coding-Standards

### Python-Stil

- **Type Hints:** Immer verwenden (`from __future__ import annotations`)
- **Async/Await:** Alle HA-Operationen müssen async sein
- **Docstrings:** Für alle öffentlichen Klassen und Methoden
- **Logging:** Modulweite Logger verwenden (`_LOGGER = logging.getLogger(__name__)`)
- **Konstanten:** Alle Magic Numbers in `const.py` auslagern
- **Fehlerbehandlung:** Spezifische Exceptions verwenden (`ConfigEntryAuthFailed`, `UpdateFailed`)

### Home Assistant Patterns

- **DataUpdateCoordinator** für Polling-basierte Datenabrufe
- **Entity Registry** für optionale Sensoren (standardmäßig deaktiviert)
- **Device Grouping** — alle Entitäten einem Gerät zuordnen
- **State Classes** korrekt setzen (`MEASUREMENT`, `TOTAL_INCREASING`)

### Commit-Nachrichten

Format: `Typ: Kurzbeschreibung`

| Typ | Verwendung |
|-----|-----------|
| `feat` | Neue Funktionalität |
| `fix` | Bugfix |
| `docs` | Nur Dokumentationsänderungen |
| `refactor` | Code-Umstrukturierung ohne Funktionsänderung |
| `test` | Tests hinzufügen oder korrigieren |
| `chore` | Build-Prozess, Abhängigkeiten, Konfiguration |

**Beispiele:**
```
feat: Unterstützung für MPPT-String 3 und 4 hinzufügen
fix: XOR-Entschlüsselung bei leerem Payload korrigieren
docs: Fehlerbehebungsabschnitt erweitern
```

### Dateistruktur

```
custom_components/ecoflow_powerocean/
├── __init__.py          # Entry point — nur Setup/Teardown
├── manifest.json        # Metadaten — Version bei neuen Releases erhöhen
├── const.py             # ALLE Konstanten — keine Magic Numbers in anderen Dateien
├── config_flow.py       # UI-Konfiguration
├── coordinator.py       # Datenabruf und MQTT-Verwaltung
├── proto_decoder.py     # Protobuf-Dekodierung — pure Python
├── sensor.py            # Sensor-Entitätsdefinitionen
├── strings.json         # Basis-Strings (Deutsch)
└── translations/
    ├── de.json          # Deutsche Übersetzung
    └── en.json          # Englische Übersetzung
```

---

## Pull Request Prozess

1. **Fork** das Repository und erstelle einen Branch vom `main`:
   ```bash
   git checkout -b feat/mein-neues-feature
   ```

2. **Implementiere** deine Änderungen und halte dich an die Coding-Standards

3. **Teste** die Änderungen gründlich:
   - Integration lädt fehlerfrei in HA
   - Keine Fehler im HA-Log
   - Alle betroffenen Sensoren funktionieren korrekt

4. **Aktualisiere** die Dokumentation wenn nötig:
   - `README.md` — neue Sensoren in die Tabellen eintragen
   - `strings.json` + `translations/de.json` + `translations/en.json` — neue Strings
   - `CHANGELOG.md` — Änderungen dokumentieren (falls vorhanden)

5. **Version erhöhen** in `manifest.json` (Semantic Versioning):
   - Patch (0.3.x): Bugfixes
   - Minor (0.x.0): Neue Sensoren, neue Features (abwärtskompatibel)
   - Major (x.0.0): Breaking Changes

6. **Pull Request erstellen** mit der PR-Vorlage

7. **Review abwarten** — mindestens ein Maintainer muss zustimmen

### PR-Checkliste

- [ ] Code folgt den Coding-Standards
- [ ] Keine hardcodierten Zugangsdaten oder persönlichen Daten
- [ ] Alle neuen Sensoren haben korrekte `device_class` und `state_class`
- [ ] Neue Strings sind in allen Übersetzungsdateien vorhanden
- [ ] `manifest.json` Version wurde aktualisiert
- [ ] README wurde aktualisiert (falls neue Sensoren/Features)
- [ ] Getestet mit einer echten EcoFlow PowerOcean Plus Anlage (wenn möglich)

---

## Besonders gesucht

### Priorität 1 — Gerätetests

Wir brauchen Tester mit:
- EcoFlow PowerOcean Plus in anderen Leistungsklassen (nicht 15 kW)
- PowerOcean Plus mit anderen Seriennummern (nicht R37*)
- PowerOcean Plus mit mehr als 2 Batterie-Packs

**Wie helfen:** Erstelle ein Issue mit deiner Konfiguration und ob die Integration funktioniert.

### Priorität 2 — Lokale Modbus TCP Integration

Der PowerOcean Plus hat Port 502 (Modbus TCP) offen. Eine lokale Kommunikation ohne EcoFlow Cloud wäre ideal:
- Protokoll-Reverse-Engineering gesucht
- Modbus-Register-Mapping erforderlich
- Würde Cloud-Abhängigkeit eliminieren

### Priorität 3 — Automatisierte Tests

- Unit-Tests für `proto_decoder.py` mit Beispieldaten aus `mqtt_raw_data.json`
- Mocking von MQTT-Verbindungen für Integrationstests

### Priorität 4 — Weitere EcoFlow-Modelle

- PowerOcean (ohne Plus)
- PowerOcean DC Fit
- Andere Heimspeicher-Serien

---

## Fragen?

Für allgemeine Fragen und Diskussionen bitte ein [GitHub Issue](https://github.com/Feberdin/ecoflow-powerocean-ha/issues) erstellen mit dem Label `question`.
