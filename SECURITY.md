# Sicherheitsrichtlinie

## Unterstützte Versionen

Sicherheitsupdates werden für die folgenden Versionen bereitgestellt:

| Version | Unterstützt |
|---------|------------|
| 0.3.x   | ✅ Aktiv   |
| < 0.3.0 | ❌ Nicht mehr unterstützt |

---

## Sicherheitslücken melden

### Bitte KEINE öffentlichen Issues für Sicherheitslücken erstellen!

Wenn du eine Sicherheitslücke entdeckst, melde sie bitte **vertraulich** über eine der folgenden Methoden:

1. **GitHub Security Advisories** (bevorzugt):
   Navigiere zu `Security` → `Advisories` → `Report a vulnerability` im Repository

2. **Direkte Nachricht:**
   Kontaktiere den Maintainer direkt über GitHub (`@Feberdin`)

### Was wir benötigen

Bitte beschreibe in deiner Meldung:

- **Art der Schwachstelle** (z. B. Credential-Leak, MQTT-Injection, unauthentifizierter Zugriff)
- **Betroffene Komponente** (z. B. `coordinator.py`, `config_flow.py`)
- **Reproduktionsschritte** — detaillierte Anleitung
- **Mögliche Auswirkungen** — was kann ein Angreifer erreichen?
- **Vorgeschlagene Lösung** (optional, aber sehr hilfreich)

### Zeitplan

| Schritt | Zeitrahmen |
|---------|-----------|
| Bestätigung der Meldung | Innerhalb von 48 Stunden |
| Erste Bewertung | Innerhalb von 7 Tagen |
| Patch/Fix bereitgestellt | Innerhalb von 30 Tagen (je nach Schweregrad) |
| Öffentliche Offenlegung | Nach Veröffentlichung des Fixes |

---

## Sicherheitsarchitektur

### Zugangsdaten-Handling

- **EcoFlow App-Zugangsdaten** (E-Mail + Passwort) werden ausschließlich im Home Assistant Config Entry gespeichert — nicht in Logs oder Konfigurationsdateien
- Das Passwort wird Base64-kodiert an die EcoFlow API übermittelt (entspricht dem Verhalten der offiziellen App)
- **MQTT-Zertifikate** sind temporäre Credentials, die regelmäßig erneuert werden
- Keine Zugangsdaten werden in `custom_components/` Dateien gespeichert

### Netzwerkkommunikation

- **HTTPS** für alle EcoFlow API-Aufrufe (`api.ecoflow.com`)
- **MQTTS (TLS 1.2+)** für MQTT-Verbindungen (`mqtt-e.ecoflow.com:8883`)
- Keine lokale Netzwerkfreigabe — alle Verbindungen gehen über EcoFlow Cloud

### Bekannte Einschränkungen

1. **Private API:** Diese Integration nutzt die inoffizielle EcoFlow Private API (App-Login). EcoFlow kann die API jederzeit ändern oder sperren.

2. **Keine 2FA-Unterstützung:** Zwei-Faktor-Authentifizierung muss in der EcoFlow App deaktiviert sein. Dies reduziert die Kontosicherheit — verwende daher ein dediziertes EcoFlow-Konto wenn möglich.

3. **Passwort-Speicherung:** Das Klartext-Passwort wird im HA Config Entry gespeichert, da es bei jedem MQTT-Credential-Refresh neu übermittelt werden muss.

### Empfehlungen für Nutzer

- Verwende ein **dediziertes EcoFlow-Konto** nur für die Home Assistant Integration
- Aktiviere **Home Assistant Authentifizierung** und sichere deinen HA-Server
- Exponiere deinen Home Assistant **nicht ungeschützt ins Internet**
- Überprüfe regelmäßig auf **Updates** dieser Integration

---

## Scope

### In Scope (relevante Sicherheitsprobleme)

- Credential-Leaks in Logs oder Konfigurationsdateien
- Unsichere MQTT-Verbindungen (fehlende TLS-Validierung)
- Code-Injection-Schwachstellen im Protobuf-Decoder
- Unbeabsichtigte Datenweitergabe an Dritte
- Privilege-Escalation in der Home Assistant Integration

### Out of Scope

- Sicherheitsprobleme in EcoFlow's eigenen Servern oder APIs
- Sicherheitsprobleme in Home Assistant selbst
- Soziale Engineering-Angriffe auf EcoFlow-Konten
- Probleme die eine physische Gerätekompromittierung erfordern

---

## Danksagung

Sicherheitsforscher, die verantwortungsvoll Schwachstellen melden, werden in der Release-Note des zugehörigen Fixes anerkannt (auf Wunsch).
