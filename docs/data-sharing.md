# Anonymisierte Messdaten teilen

Dieses Dokument beschreibt ein kleines JSON-Format, mit dem Nutzer oder andere
Repos Messpunkte zur EcoFlow-PowerOcean-Auswertung teilen können.

## Zweck

Die Integration kann nur besser werden, wenn reale Anlagenzustände vergleichbar
werden: PV-Erzeugung, Hausverbrauch, Netzbezug/Einspeisung, Batterieleistung,
SOC und optionale Statuswerte. Das Format ist bewusst einfach gehalten und
benötigt keine zusätzliche Abhängigkeit.

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

- `schema_version`: aktuell immer `1`
- `observed_at`: Zeitpunkt des Messpunkts als ISO-8601-Zeitstempel
- `source`: anonymisierte Angaben zur Quelle
- `measurements`: Leistungs- und SOC-Werte
- `status`: optionale Statuswerte der Integration oder eines Fremdrepos
- `notes`: optionale Freitextnotiz ohne private Daten

## Beispiel für andere Repos

Andere Projekte können dasselbe JSON erzeugen und in einem GitHub-Issue
anhängen oder als Pull Request mit einem anonymisierten Beispieldatensatz
einreichen.

Minimal hilfreich ist:

```json
{
  "schema_version": 1,
  "observed_at": "2026-05-29T21:30:00+02:00",
  "source": {
    "source_id": "anonymous-sample-1",
    "repo": "example/powerocean-tool"
  },
  "measurements": {
    "total_soc_percent": 10.0,
    "solar_power_w": 0.0,
    "grid_power_w": 180.0,
    "load_power_w": 180.0,
    "battery_power_w": 0.0
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

## Validierung ohne Zusatzabhängigkeiten

Dieses Repo bringt bewusst keinen JSON-Schema-Validator als Abhängigkeit mit.
Vor dem Teilen sollte die Datei mindestens gültiges JSON sein:

```bash
python3 -m json.tool examples/powerocean-observation.example.json >/dev/null
python3 -m json.tool schemas/powerocean-observation.schema.json >/dev/null
```

Bei Pull Requests prüfen wir zusätzlich, ob keine Seriennummern, Tokens oder
anderen privaten Daten enthalten sind.
