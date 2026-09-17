# Energiequalität 0.4.25

## Fachliche Korrekturen

Energiezähler integrieren nur kurze, durch gültige Messwerte begrenzte Intervalle.
Eine Verbindungslücke ist keine Messung. Alte Lücken wurden mit einem Trapez
zwischen der letzten und nächsten Leistung geschätzt; über Nacht konnte das
fiktive PV-Produktion erzeugen. Bestehende Zählerstände bleiben erhalten.

EMS-Phasen sind laut Protokoll `pcsAPhase`/`pcsBPhase`/`pcsCPhase`, also
Wechselrichterwerte. Sie werden nicht mehr als Messung am Netzanschluss
ausgegeben. Ohne frischen Energy Stream bleiben Netz und Hauslast unbekannt.
Solar- und Batteriewerte können aus einem aktuellen EMS weiter verfügbar sein.

Der Decoder unterstützt zusätzlich frische `EnergyStreamDetail`-Telegramme
(96/34). Gerätestempel werden statt Ankunftszeit gespeichert; alte, zukünftige,
ungeordnete und nicht endliche Samples werden defensiv behandelt.
Primäre Protokollreferenz:
[ioBroker EcoFlow, PowerOcean Plus](https://github.com/foxthefox/ioBroker.ecoflow-mqtt/blob/main/lib/dict_data/ef_poweroceanplus_data.js).

## Rot-/Grün-Nachweis

```sh
python3 -m unittest discover -s tests -p test_energy_quality.py -v
python3 -m unittest discover -s tests -p test_energy_stream_detail.py -v
python3 -m unittest discover -s tests -p test_energy_coordinator.py -v
python3 -m unittest discover -s tests -p test_daily_report.py -k unknown_grid -v
```

Beobachtete fachliche Fehler vor dem Fix:

- Zwölf Stunden synthetische Verbindungslücke erzeugten 18 kWh statt keiner
  gemessenen Energie; eine stille Stunde erzeugte 1 kWh.
- Ungültige Leistung wurde als gültiger Zustand behandelt.
- PCS-Leistung wurde als Netzleistung ausgegeben.
- Alte EMS-Solarleistung blieb trotz frischerer Batteriepakete aktiv.
- Fehlender Netzleistungswert galt als ruhiges Netz.
- Gültige Detailtelegramme wurden ignoriert.
- Gerätemesszeit wurde durch Empfangszeit ersetzt.
- Der Tagesbericht schrieb die letzte Einspeisung über fehlende Daten fort.
- Leistungssensoren boten keinen echten Messzeitpunkt für Automationen.

Danach alle fokussierten Tests grün; vollständige Suite: 63 Tests.
Zwei ältere EMS-Testfälle wurden auf den korrigierten Vertrag umgestellt:
explizit unbekannte Netz-/Hausleistung statt behaupteter PCS-Netzmessung.
Die Solar-/Batterieassertions bleiben unverändert stark.

Gesamtprüfung:

```sh
python3 -m unittest discover -s tests
python3 -m compileall -q custom_components/ecoflow_powerocean
git diff --check
docker run --rm -v "$PWD:/github/workspace" ghcr.io/home-assistant/hassfest
```

Kein separater Bundle-/TypeScript-Build oder projektweiter Python-Linter
konfiguriert. Die neuen Tests benötigen ausschließlich die Standardbibliothek.
HA und MQTT werden nur an ihren Grenzen ersetzt; Decoder, Callback und
Akkumulatorlogik laufen unverändert aus dem Produktionscode.

## Live-Abnahme und Grenzen

Nach HACS-Update HA neu starten, Manifestversion prüfen und in den
Leistungsattributen `power_source`/`power_observed_at` kontrollieren. Bei
`unavailable` keine Laderegelung aus `float(0)` ableiten. Geräte-App, momentanen
Netzzähler und Zählerdifferenzen unabhängig vergleichen. Die korrekte Zuordnung
des Protokolls ersetzt keine elektrische Messvalidierung vor Ort.

Das Update repariert keine alten Statistikwerte und rekonstruiert keine Lücken.
SOC-Schutz aus 0.4.24 bleibt aktiv. Neue Detailtelegramme steuern kein Gerät.
Schreiboptionen bleiben unverändert. DEBUG zeigt ausgelassene Intervalle und
Quelltypen, keine Rohpakete, Seriennummern oder Anmeldedaten.
