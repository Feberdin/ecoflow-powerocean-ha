# Anonymisierte Messdaten teilen

Dieses Dokument beschreibt ein anonymisiertes JSON-Format, mit dem Nutzer oder
andere Repos Messpunkte zur EcoFlow-PowerOcean-Auswertung teilen können.

## Zweck

Die Integration kann nur besser werden, wenn reale Anlagenzustände vergleichbar
werden: PV-Erzeugung, Hausverbrauch, Netzbezug/Einspeisung, Batterieleistung,
SOC, Energiezähler, Batteriepackdaten, Backup-Schätzung, Tagesberichtswerte
und Statuswerte. Das Format benötigt keine zusätzliche Abhängigkeit und kann mit
dem Script `tools/build_anonymized_observation.py` erzeugt werden.

## Datenschutz

Bitte niemals diese Daten teilen:

- Seriennummern oder vollständige Geräte-IDs
- E-Mail-Adressen, Namen, Adressen oder genaue GPS-/Standortdaten
- Tokens, Cookies, Passwörter oder MQTT-Zugangsdaten
- Home-Assistant-Backupdateien oder rohe `.storage`-Dateien
- Logs, die private Werte enthalten

Stattdessen bitte nur eine anonymisierte Quellen-ID verwenden, z. B.
`"source_id": "user-issue-12-sample-a"`.

## Format

Siehe Beispiel:

- [`examples/powerocean-observation.example.json`](../examples/powerocean-observation.example.json)

Die zugehörige JSON-Schema-Datei liegt hier:

- [`schemas/powerocean-observation.schema.json`](../schemas/powerocean-observation.schema.json)

Wichtige Felder:

- `schema_version`: aktuell immer `2`
- `observed_at`: Zeitpunkt des Messpunkts als ISO-8601-Zeitstempel
- `source`: anonymisierte Angaben zur Quelle
- `measurements.power`: aktuelle Live-Leistungswerte
- `measurements.energy`: Langzeit-Energiezähler
- `measurements.battery`: Gesamt-SOC und Batteriepackdaten
- `measurements.backup`: Backup-Laufzeit und nutzbare Reserve
- `measurements.daily_report`: Tagesbericht-Statistiken
- `measurements.system_limits`: ausgelesene Systemgrenzen
- `status`: optionale Statuswerte der Integration oder eines Fremdrepos
- `privacy`: welche Redaktionen angewendet wurden
- `entity_sources`: nur anonymisierte Quellenhinweise, keine vollständigen Entity-IDs
- `notes`: optionale Freitextnotiz ohne private Daten

## Export aus Home Assistant

Wenn du einen lokalen Snapshot aus Home Assistant erzeugen möchtest:

```bash
HOMEASSISTANT_TOKEN=... \
python3 tools/build_anonymized_observation.py \
  --ha-url http://homeassistant.local:8123 \
  --source-id sample-a \
  --integration-version v0.4.21 \
  --home-assistant-version 2026.7.4 \
  --battery-packs 3 \
  --backup-reserved-soc-percent 10 \
  --reserve-guard-restart-margin-percent 2
```

Wichtig: Den Token nur als Umgebungsvariable verwenden und nicht in Dateien,
Issues oder Logs schreiben.

Wenn du bereits eine `/api/states`-Datei hast:

```bash
python3 tools/build_anonymized_observation.py \
  --states states.json \
  --source-id sample-a
```

Das Script entfernt keine Rohdatei und schreibt standardmäßig nichts auf die
Festplatte. Es gibt das anonymisierte JSON auf stdout aus.

## Beispiel für andere Repos

Andere Projekte können dasselbe JSON erzeugen und in einem GitHub-Issue
anhängen oder als Pull Request mit einem anonymisierten Beispieldatensatz
einreichen.

Minimal hilfreich ist weiterhin ein `measurements.power`-Block plus SOC. Besser
ist ein vollständiger Snapshot wie in
[`examples/powerocean-observation.example.json`](../examples/powerocean-observation.example.json).

```json
{
  "schema_version": 2,
  "observed_at": "2026-05-29T21:30:00+02:00",
  "source": {
    "source_id": "anonymous-sample-1",
    "repo": "example/powerocean-tool"
  },
  "measurements": {
    "power": {
      "solar_power_w": 0.0,
      "grid_power_w": 180.0,
      "load_power_w": 180.0,
      "battery_power_w": 0.0
    },
    "battery": {
      "total_soc_percent": 10.0
    }
  },
  "privacy": {
    "redaction_level": "entity_id_suffix_only",
    "removed": ["serial_numbers", "tokens", "email_addresses", "full_entity_ids"]
  }
}
```

## Interpretation

Positive und negative Vorzeichen unterscheiden sich je nach Quelle. Für dieses
Format gilt:

- `grid_power_w` positiv: Netzbezug
- `grid_power_w` negativ: Einspeisung
- `battery_power_w` positiv: Batterie entlädt
- `battery_power_w` negativ: Batterie lädt
- `solar_power_w` positiv: PV-Erzeugung
- `load_power_w` positiv: Hausverbrauch

Wenn eine Quelle diese Vorzeichen nicht sicher liefern kann, bitte in `notes`
kurz beschreiben, wie die Werte zu lesen sind.

## Was bewusst nicht geteilt wird

Das Export-Script schreibt keine vollständigen Entity-IDs in `entity_sources`.
Aus `sensor.garage_ecoflow_powerocean_plus_netz_leistung` wird z. B. nur:

```json
{
  "domain": "sensor",
  "matched_suffix": "netz_leistung"
}
```

So bleibt nachvollziehbar, welches Feld erkannt wurde, ohne Räume, Namen oder
eigene Entity-Benennungen mitzuveröffentlichen.

## Validierung ohne Zusatzabhängigkeiten

Dieses Repo bringt bewusst keinen JSON-Schema-Validator als Abhängigkeit mit.
Vor dem Teilen sollte die Datei mindestens gültiges JSON sein:

```bash
python3 -m json.tool examples/powerocean-observation.example.json >/dev/null
python3 -m json.tool schemas/powerocean-observation.schema.json >/dev/null
```

Bei Pull Requests prüfen wir zusätzlich, ob keine Seriennummern, Tokens oder
anderen privaten Daten enthalten sind.
