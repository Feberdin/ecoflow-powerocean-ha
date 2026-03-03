# Pull Request

## Beschreibung

<!-- Beschreibe die Änderungen in diesem PR klar und prägnant -->

## Art der Änderung

- [ ] 🐛 Bugfix (behebt ein Problem ohne Breaking Change)
- [ ] ✨ Neues Feature (fügt Funktionalität hinzu ohne Breaking Change)
- [ ] 💥 Breaking Change (bestehende Funktionalität wird verändert)
- [ ] 📚 Dokumentation (nur Dokumentationsänderungen)
- [ ] 🔧 Refactoring (Code-Umstrukturierung ohne Funktionsänderung)
- [ ] 🧪 Tests (fügt Tests hinzu oder korrigiert sie)

## Verwandtes Issue

<!-- Schließt # (Issue-Nummer) -->

## Getestet mit

| Gerät | Seriennummer (erste 4 Zeichen) | Batterie-Packs | Ergebnis |
|-------|-------------------------------|---------------|---------|
| PowerOcean Plus | | | ✅ / ❌ / ⚠️ |

## Checkliste

### Code-Qualität
- [ ] Typ-Hinweise (`from __future__ import annotations`) vorhanden
- [ ] Keine hardcodierten Magic Numbers (alle in `const.py`)
- [ ] Logging über `_LOGGER` (kein `print()`)
- [ ] Async/Await korrekt verwendet
- [ ] Fehlerbehandlung mit spezifischen HA-Exceptions

### Home Assistant Patterns
- [ ] Neue Sensoren haben korrekte `device_class` und `state_class`
- [ ] Neue Sensoren sind korrekt dem Gerät zugeordnet
- [ ] Optionale Sensoren sind standardmäßig deaktiviert (`entity_registry_enabled_default=False`)

### Lokalisierung
- [ ] Neue Strings in `strings.json` eingetragen
- [ ] Neue Strings in `translations/de.json` übersetzt
- [ ] Neue Strings in `translations/en.json` übersetzt

### Dokumentation
- [ ] `README.md` aktualisiert (neue Sensoren in Tabellen eingetragen)
- [ ] `manifest.json` Version entsprechend Semantic Versioning erhöht
- [ ] Keine persönlichen Daten, Zugangsdaten oder Seriennummern im Code

### Tests
- [ ] Integration lädt fehlerfrei in Home Assistant
- [ ] Keine Fehler/Warnungen im HA-Log
- [ ] Alle betroffenen Sensoren liefern korrekte Werte

## Screenshots / Logs

<!-- Falls relevant: Screenshots der neuen Sensoren, Ausschnitte aus HA-Logs -->

## Zusätzliche Informationen

<!-- Weitere Kontext, Designentscheidungen, bekannte Einschränkungen -->
