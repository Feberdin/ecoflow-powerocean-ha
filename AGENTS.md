# AGENTS.md

Zweck:
Diese Datei ist der Projekt-Grundstock fuer kuenftige Codex-Sessions im Repo
`Feberdin/ecoflow-powerocean-ha`. Sie sammelt Architekturregeln, bekannte
Home-Assistant-Kompatibilitaetsfallen und den bewaehrten Release-/Debug-Ablauf.

Input:
- Lokaler Repo-Stand unter `ha_integration`
- Home-Assistant-Logs und Nutzerberichte
- GitHub Releases/Tags im Repo `Feberdin/ecoflow-powerocean-ha`

Output:
- Sichere, kleine Aenderungen an der Custom Integration
- Nachvollziehbare Tests und Releases
- Keine Secrets, Tokens, User-IDs oder echten Geraetedaten in Chat, Git oder Logs

Wichtige Invarianten:
- Domain bleibt `ecoflow_powerocean`.
- `hass.data[DOMAIN][entry.entry_id]` bleibt der `EcoFlowCoordinator`.
- Neue Manager/Zusatzdaten separat speichern, z. B. unter eigenem `hass.data`-Key.
- Bestehende Sensoren, Entity-IDs und Energie-Dashboard-Logik nicht umbenennen.
- Keine neuen externen Dependencies einfuehren, wenn es nicht zwingend noetig ist.
- Feature-Optionen standardmaessig deaktiviert halten.

Debug-Hinweis:
- Bei HA-UI-Fehlern zuerst den echten Python-Traceback suchen. UI-Meldungen wie
  `400: Bad Request` oder `500 Internal Server Error` sind nur Symptome.
- Wenn HACS angeblich aktualisiert hat, in HA die installierte Manifest-Version
  pruefen: `grep '"version"' /config/custom_components/ecoflow_powerocean/manifest.json`.

## Projektstruktur

- `custom_components/ecoflow_powerocean/__init__.py`: Setup, Unload, Plattformen.
- `custom_components/ecoflow_powerocean/coordinator.py`: EcoFlow API/MQTT und Datenhaltung.
- `custom_components/ecoflow_powerocean/sensor.py`: Sensoren und Energie-Akkumulatoren.
- `custom_components/ecoflow_powerocean/backup_helpers.py`: reine Helper-Logik.
- `custom_components/ecoflow_powerocean/daily_report.py`: optionaler Tagesbericht.
- `custom_components/ecoflow_powerocean/config_flow.py`: Einrichtung und Options Flow.
- `custom_components/ecoflow_powerocean/const.py`: zentrale Konstanten und Defaults.
- `tests/`: leichte Unit-Tests ohne kompletten HA-Teststack.

## Tagesbericht: Stand und Fallstricke

- Default-Tarif: `DEFAULT_DAILY_REPORT_FEED_IN_TARIFF_EUR_PER_KWH = 0.077`.
- Berechnung: `value_eur = daily_export_kwh * feed_in_tariff_eur_per_kwh`.
- Einspeisung wird wie in den Sensoren aus `max(-grid_power_w(data), 0.0)` abgeleitet.
- SOC kommt aus `total_soc_percent(data)`.
- Akkustand-100%-Dauer wird nur bei vorherigem SOC `>= 100.0` gezaehlt.
- Lange MQTT-/HA-Luecken defensiv begrenzen; keine scheinbar exakten Fantasiewerte.
- Storage-Schreibzugriffe drosseln und bei Sunset/Unload erzwingen.

## Notify-Ziel im Options Flow

Aktuell ist das Feld `Benachrichtigungsziel` bewusst ein Textfeld. Erwartet wird
eine Notify-Entity-ID, z. B.:

```text
notify.mobile_app_beispiel_iphone
```

Nicht eintragen:
- Anzeigename des Geraets
- `device_tracker...`
- kompletter YAML-Block

Manueller HA-Test:

```yaml
service: notify.send_message
target:
  entity_id: notify.mobile_app_beispiel_iphone
data:
  title: EcoFlow Test
  message: Testnachricht
```

Warum kein TargetSelector:
- In den getesteten HA-Versionen verursachten Selector-/Schema-Unterschiede
  wiederholt `400: Bad Request` beim Laden des Options Flow.
- Ein Textfeld mit Normalisierung auf `{"entity_id": value}` ist robuster.

## Options-Flow-Kompatibilitaet

Bekannte Fix-Historie:
- `ConfigFlowResult` nicht zur Laufzeit importieren; in HA 2024.1 fehlt dieser
  Importpfad. Nur unter `TYPE_CHECKING` verwenden.
- `OptionsFlow` in HA 2024.1 braucht einen expliziten Config-Entry-Verweis.
- Neuere HA-Versionen verwalten `OptionsFlow.config_entry` intern. Deshalb
  niemals `self.config_entry = config_entry` setzen. Stattdessen eigenen
  Verweis nutzen, z. B. `self._entry = config_entry`.
- Kleine `NumberSelector`-Steps wie `0.0001` koennen in HA 2024.1 am Selector-
  Schema scheitern. Fuer den Tarif wurde deshalb ein Textfeld verwendet und
  die Eingabe normalisiert. Komma und Punkt als Dezimaltrenner akzeptieren.

Regressionstest fuer Options Flow:

```bash
PYTHONPATH="$PWD" /tmp/ecoflow-ha-flow-test/bin/python - <<'PY'
import asyncio
from homeassistant.const import __version__ as HA_VERSION
from custom_components.ecoflow_powerocean.config_flow import EcoFlowPowerOceanConfigFlow
from custom_components.ecoflow_powerocean.const import CONF_ENABLE_DAILY_SUNSET_REPORT

class Entry:
    data = {}
    options = {CONF_ENABLE_DAILY_SUNSET_REPORT: False}

async def main():
    flow = EcoFlowPowerOceanConfigFlow.async_get_options_flow(Entry())
    result = await flow.async_step_init()
    print("HA", HA_VERSION)
    print(result.get("type"), result.get("step_id"), len(list(result["data_schema"].schema)))

asyncio.run(main())
PY
```

Erwartung: `form init 11`.

## HACS und Releases

- Fuer installierbare Aenderungen `manifest.json` Version erhoehen.
- Git taggen und GitHub Release erstellen, sonst erkennt HACS oft kein Update.
- Nach jedem `git push` zuerst den GitHub-Actions-Run fuer den gepushten Commit
  pruefen und CI-/Validation-Fehler beheben, bevor eine neue Aufgabe begonnen wird.
- Repo und Commit nach Push ermitteln:

```bash
gh repo view --json nameWithOwner -q .nameWithOwner
git rev-parse HEAD
```

- Passenden Run abwarten:

```bash
gh run list --commit <SHA> --limit 5 --json databaseId,name,status,conclusion,headSha,url
gh run watch <RUN_ID> --compact --exit-status
```

- Bei Fehlern die erste echte Fehlermeldung aus den fehlgeschlagenen Logs lesen:

```bash
gh run view <RUN_ID> --log-failed
```

- Fehler lokal beheben, committen, erneut pushen und den Zyklus wiederholen,
  bis der GitHub-Actions-Run fuer den neuen Commit gruen ist.
- Nach Release pruefen:

```bash
python3 - <<'PY'
import json, urllib.request
url = "https://api.github.com/repos/Feberdin/ecoflow-powerocean-ha/releases/latest"
with urllib.request.urlopen(url, timeout=20) as response:
    data = json.load(response)
print(data["tag_name"], data["html_url"])
PY
```

- Bei reinen Projekt-/Arbeitsnotizen keine Integration-Version erhoehen, weil
  sich der installierbare HA-Code nicht aendert.

## Hassfest-/HACS-Regeln fuer dieses Repo

- Vor jedem Push `custom_components/ecoflow_powerocean/manifest.json` als JSON
  validieren.
- Manifest-Key-Reihenfolge fuer Hassfest:
  `domain`, `name`, `after_dependencies`, `codeowners`, `config_flow`,
  `dependencies`, `documentation`, `integration_type`, `iot_class`,
  `issue_tracker`, `requirements`, `version`.
- `custom_components/ecoflow_powerocean/brand/icon.png` muss als quadratische
  PNG-Datei existieren.
- Lokal Hassfest ausfuehren:

```bash
docker run --rm -v "$PWD:/github/workspace" ghcr.io/home-assistant/hassfest
```

- Keine Secrets, Tokens, privaten Logs oder `.env`-Dateien committen.

## Validierung

Vor Code-Releases ausfuehren:

```bash
python3 -m unittest discover -s tests
python3 -m py_compile custom_components/ecoflow_powerocean/*.py
git diff --check
```

Bei Config-Flow-Aenderungen zusaetzlich gegen HA 2024.1 und eine neuere HA-
Version testen, wenn lokale venvs vorhanden sind:

```bash
PYTHONPATH="$PWD" /tmp/ecoflow-ha-flow-test/bin/python <options-flow-test.py>
PYTHONPATH="$PWD" /tmp/ecoflow-ha-current-test/bin/python <options-flow-test.py>
```

## Git-Arbeitsweise

- Im lokalen Arbeitsbaum koennen viele reine Dateimodus-Aenderungen (`100644`
  zu `100755`) auftauchen. Diese nicht versehentlich committen.
- Beim Stagen gezielt arbeiten:

```bash
git add --chmod=-x path/to/file
```

- Keine fremden/unrelated Aenderungen zuruecksetzen.
- Nach Commit pushen, wenn der Nutzer eine testbare GitHub-Version erwartet.

## Log- und Datenschutzregeln

- EcoFlow Tokens, User-IDs, MQTT-Accounts, echte Seriennummern und persoenliche
  Ziel-Entitys nicht in README, Issues, Release Notes oder Chat wiederholen.
- Logs duerfen lokal durchsucht werden, aber Antworten nur mit redigierten
  Erkenntnissen formulieren.
- Bei Sendefehlern im Tagesbericht warnen, aber die Integration nicht crashen.

## Schnelle Fehlerdiagnose

Wenn `Konfigurieren` nicht laedt:
1. Manifest-Version in HA pruefen.
2. HA vollstaendig neu starten.
3. HA-Log direkt nach dem Klick auf `Konfigurieren` auswerten.
4. Gezielt diese Logger aktivieren:

```yaml
logger:
  default: warning
  logs:
    homeassistant.config_entries: debug
    homeassistant.helpers.data_entry_flow: debug
    custom_components.ecoflow_powerocean: debug
    custom_components.ecoflow_powerocean.config_flow: debug
```

Bekannte Symptome:
- `400: Bad Request`: haeufig Selector-/Schema-Problem im Options Flow.
- `500 Internal Server Error`: haeufig echte Python-Exception im Flow, z. B.
  inkompatible HA-OptionsFlow-API.
