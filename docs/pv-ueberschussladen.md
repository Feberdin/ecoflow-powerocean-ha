# PV-Ueberschussladen mit EcoFlow PowerOcean

Zweck:
Diese Anleitung zeigt, wie du die Sensoren der EcoFlow-PowerOcean-Integration
fuer eine Home-Assistant-Automation zum PV-Ueberschussladen nutzt.

Input:
- EcoFlow-Sensoren fuer Solarleistung, Hausverbrauch, Netzleistung und Speicher-SOC
- Wallbox-Entitaeten fuer Ladefreigabe und optional Ladestrom
- optional Fahrzeug-SOC und Fahrzeug-Ladelimit

Output:
- Eine Home-Assistant-Automation, die bei PV-Ueberschuss eine Wallbox startet,
  den Ladestrom regelt und bei zu wenig Sonne oder zu niedrigem Speicher-SOC stoppt

Wichtige Invarianten:
- Die Integration steuert keine Wallbox direkt.
- Die Automation gehoert in deine Home-Assistant-Konfiguration.
- Entity-IDs muessen an deine Installation angepasst werden.
- Werte sind Komfort-/Monitoringwerte und ersetzen keine elektrische Schutzlogik.

Debug-Hinweis:
- Starte mit kleinen Grenzen und pruefe jede Entity in
  `Entwicklerwerkzeuge -> Zustaende`.
- Nutze in Home Assistant die Automation-Traces, um Start-/Stop-Entscheidungen
  nachzuvollziehen.

---

## Idee

Die EcoFlow-Integration liefert die Energiesituation im Haus:

- Wie viel PV-Leistung ist gerade vorhanden?
- Wie viel verbraucht das Haus ohne Wallbox?
- Wird gerade ins Netz eingespeist?
- Ist der Speicher schon voll genug?

Eine Home-Assistant-Automation kann daraus entscheiden:

- Wallbox starten, wenn genug PV-Ueberschuss vorhanden ist
- Ladestrom passend zum Ueberschuss setzen
- Laden stoppen, wenn Speicher-SOC, Sonne oder Ueberschuss nicht mehr reichen
- optional in einem festen Zeitfenster vor langen Fahrten mit Maximalstrom laden

Die Beispielvorlage basiert auf einem go-eCharger und einem Fahrzeug-SOC-Sensor.
Du kannst sie aber auf andere Wallboxen oder Verbraucher uebertragen.

---

## Voraussetzungen

Du brauchst in Home Assistant:

- EcoFlow PowerOcean Sensoren fuer Solar, Hausverbrauch, Netzleistung und Speicher-SOC
- eine Wallbox-Integration oder steuerbare Schalt-/Relais-Entitaet
- optional Fahrzeug-Sensoren fuer SOC und Ladelimit
- `sun.sun` fuer die Sonnenhoehe

Die Vorlage liegt hier:

```text
examples/pv-ueberschussladen-goecharger.yaml
```

---

## Entity-IDs anpassen

Ersetze in der Vorlage mindestens diese Platzhalter:

| Platzhalter | Bedeutung | Beispiel |
|-------------|-----------|----------|
| `sensor.mein_powerocean_solar_leistung` | aktuelle PV-Leistung in W | `sensor.ecoflow_powerocean_plus_solar_leistung` |
| `sensor.mein_powerocean_hausverbrauch` | aktueller Hausverbrauch in W | `sensor.ecoflow_powerocean_plus_hausverbrauch` |
| `sensor.mein_powerocean_netz_leistung` | Netzleistung, negativ bei Einspeisung | `sensor.ecoflow_powerocean_plus_netz_leistung` |
| `sensor.mein_powerocean_gesamt_ladestand` | Speicher-SOC in % | `sensor.ecoflow_powerocean_plus_gesamt_ladestand` |
| `switch.meine_wallbox_ladefreigabe` | Ladefreigabe der Wallbox | `switch.goecharger_allow_charging` |
| `sensor.meine_wallbox_leistung_kw` | aktuelle Ladeleistung der Wallbox in kW | `sensor.goecharger_p_all` |
| `sensor.meine_wallbox_ladestrom_l1/l2/l3` | Phasenstroeme der Wallbox in A | go-eCharger Stromsensoren |
| `sensor.mein_auto_ladestand` | Fahrzeug-SOC in % | Tesla, VW, Kia, BMW usw. |
| `number.mein_auto_ladelimit` | optionales Fahrzeug-Ladelimit | Tesla-Ladelimit |

Wenn dein Fahrzeug keinen SOC liefert, kannst du die Fahrzeug-SOC-Teile aus der
Automation entfernen oder `car_target_soc` so behandeln, dass nur die Wallbox
entscheidet.

---

## Wichtige Stellschrauben

| Variable | Bedeutung | Typischer Startwert |
|----------|-----------|---------------------|
| `car_target_soc` | Ziel-Ladestand des Autos | `80` bis `100` |
| `storage_start_soc` | Speicher-SOC, ab dem Laden starten darf | `95` bis `100` |
| `storage_stop_soc` | Speicher-SOC, unter dem Laden stoppt | `85` bis `95` |
| `min_solar_w` | Mindest-PV-Leistung fuer Start | `500` |
| `min_solar_keep_w` | Mindest-PV-Leistung zum Weiterladen | `200` |
| `min_export_w` | Mindest-Einspeisung als Start-/Haltehinweis | `100` |
| `watts_per_amp` | Leistung pro Ampere bei deiner Wallbox | ca. `230` einphasig, ca. `690` dreiphasig |
| `min_a` | Mindestladestrom der Wallbox | meistens `6` |
| `max_a` | Maximaler Ladestrom | z. B. `16` |
| `long_trip_*` | optionales Zeitfenster fuer geplante Langstrecken | nach Bedarf |

Warum `storage_start_soc` und `storage_stop_soc` getrennt sind:
Das erzeugt eine Hysterese. Die Wallbox startet erst bei hohem Speicher-SOC und
stoppt erst bei niedrigerem SOC. Dadurch schaltet sie nicht jede Minute an/aus.

---

## Uebertragung auf andere Wallboxen

Die Beispielautomation trennt bewusst drei Aufgaben:

1. Messwerte lesen
2. Entscheidung berechnen
3. Wallbox schalten oder regeln

Fuer andere Wallboxen ersetzt du vor allem diese Stellen:

```yaml
service: goecharger.set_max_current
data:
  max_current: "{{ target_a | int }}"
```

und:

```yaml
service: switch.turn_on
target:
  entity_id: switch.meine_wallbox_ladefreigabe
```

Bei einer Wallbox mit `number`-Entitaet fuer Ladestrom nutzt du stattdessen zum
Beispiel:

```yaml
service: number.set_value
target:
  entity_id: number.meine_wallbox_ladestrom
data:
  value: "{{ target_a | int }}"
```

Wenn deine Wallbox nur ein/aus kann, laesst du die Ladestrom-Regelung weg und
nutzt nur die Start-/Stop-Bloecke.

---

## Uebertragung auf andere Verbraucher

Fuer steuerbare Verbraucher wie Heizstab, Boiler, Klimageraet oder Steckdose ist
dieselbe Grundlogik nutzbar:

- `storage_start_soc`: Verbraucher erst starten, wenn der Speicher ausreichend voll ist
- `storage_stop_soc`: Verbraucher stoppen, bevor der Speicher zu stark entladen wird
- `free_solar_w` und `export_w`: nur bei echtem Ueberschuss einschalten

Fuer einfache Schaltlasten entfernst du alle Ampere-Variablen:

- `watts_per_amp`
- `min_a`
- `max_a`
- `current_a`
- `target_a`

Dann bleibt nur:

```yaml
service: switch.turn_on
target:
  entity_id: switch.mein_verbraucher
```

und:

```yaml
service: switch.turn_off
target:
  entity_id: switch.mein_verbraucher
```

---

## Debugging

Wenn die Automation nicht startet:

1. In `Entwicklerwerkzeuge -> Zustaende` pruefen, ob alle Entity-IDs existieren.
2. Pruefen, ob `sensor.mein_powerocean_netz_leistung` bei Einspeisung negativ ist.
3. In der Automation auf `Spuren` klicken und die berechneten Variablen ansehen.
4. `storage_start_soc` testweise niedriger setzen, z. B. auf `80`.
5. `min_solar_w` testweise niedriger setzen, z. B. auf `200`.
6. Bei Wallboxen pruefen, ob externe Steuerung per Home Assistant erlaubt ist.

Wenn die Wallbox zu oft an/aus schaltet:

- Abstand zwischen `storage_start_soc` und `storage_stop_soc` vergroessern
- `min_solar_keep_w` erhoehen
- optional den Trigger von jede Minute auf alle 2 bis 5 Minuten stellen

Wenn der Ladestrom zu stark springt:

- `battery_rounding_w` verkleinern oder entfernen
- `watts_per_amp` an deine Phasenanzahl anpassen
- bei einphasigem Laden ca. `230`, bei dreiphasigem Laden ca. `690` verwenden

---

## Hinweis zur Sicherheit

Die Automation ist ein Komfortbeispiel fuer Home Assistant. Ladeinfrastruktur
und elektrische Schutzfunktionen muessen weiterhin durch Wallbox, Installation
und Elektrofachbetrieb abgesichert sein.
