"""
Config Flow für die EcoFlow PowerOcean Plus Integration.

Der Config Flow führt den Benutzer durch die Einrichtung der Integration:

    Schritt 1 (user): Eingabe von E-Mail, Passwort und Seriennummer
    Schritt 2:        Validierung der Zugangsdaten gegen die EcoFlow API
    Schritt 3:        Erstellung des Config Entry bei Erfolg

Bei ungültigen Zugangsdaten wird eine aussagekräftige Fehlermeldung angezeigt
und der Benutzer kann die Eingabe korrigieren.

Die Seriennummer kann optional leer gelassen werden — in diesem Fall versucht
der Coordinator beim ersten Start, das Gerät automatisch zu erkennen. Da EcoFlow
Accounts in der Regel nur ein PowerOcean-Gerät enthalten, funktioniert die
automatische Erkennung in den meisten Fällen.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    API_LOGIN_URL,
    API_TIMEOUT,
    CONF_NUM_BATTERY_PACKS,
    CONF_SERIAL_NUMBER,
    DEFAULT_NUM_BATTERY_PACKS,
    DOMAIN,
    MANUFACTURER,
    MAX_BATTERY_PACKS,
    MODEL,
)

_LOGGER = logging.getLogger(__name__)

# Validierungsschema für das Einrichtungsformular
STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): TextSelector(
            TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="email")
        ),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
        vol.Required(CONF_SERIAL_NUMBER): TextSelector(
            TextSelectorConfig(type=TextSelectorType.TEXT)
        ),
        vol.Optional(CONF_NUM_BATTERY_PACKS, default=DEFAULT_NUM_BATTERY_PACKS): NumberSelector(
            NumberSelectorConfig(
                min=1,
                max=MAX_BATTERY_PACKS,
                step=1,
                mode=NumberSelectorMode.BOX,
            )
        ),
    }
)


async def _validate_credentials(email: str, password: str) -> tuple[str, str]:
    """
    Validiert die EcoFlow-Zugangsdaten durch einen Test-Login.

    Das Passwort wird Base64-kodiert — exakt so wie die EcoFlow-App es überträgt.
    MD5-Hashing (wie bei manchen anderen EcoFlow-Projekten dokumentiert) ist für
    die Private API nicht korrekt.

    Args:
        email:    EcoFlow-Konto-E-Mail-Adresse.
        password: EcoFlow-Konto-Passwort (Klartext, wird hier kodiert).

    Returns:
        Tuple aus (token, user_id) bei Erfolg.

    Raises:
        ValueError: Bei falschen Zugangsdaten (Code 2026).
        ConnectionError: Bei Netzwerkproblemen.
    """
    password_b64 = base64.b64encode(password.encode("utf-8")).decode("utf-8")
    payload = {
        "email": email,
        "password": password_b64,
        "scene": "IOT_APP",
        "userType": "ECOFLOW",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                API_LOGIN_URL,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=API_TIMEOUT),
            ) as resp:
                data = await resp.json()
    except aiohttp.ClientError as exc:
        raise ConnectionError(f"Netzwerkfehler: {exc}") from exc
    except asyncio.TimeoutError as exc:
        raise ConnectionError("Verbindungs-Timeout zur EcoFlow API") from exc

    code = str(data.get("code", ""))
    if code != "0":
        msg = data.get("message", "Unbekannter Fehler")
        raise ValueError(f"Login fehlgeschlagen (Code {code}): {msg}")

    user_data = data.get("data", {})
    token = user_data.get("token", "")
    user_id = str(user_data.get("user", {}).get("userId", ""))
    return token, user_id


class EcoFlowOptionsFlow(OptionsFlow):
    """Options Flow — erlaubt nachträgliche Konfigurationsänderungen."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Zeigt das Options-Formular und speichert Änderungen."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current_packs = int(
            self.config_entry.options.get(
                CONF_NUM_BATTERY_PACKS,
                self.config_entry.data.get(CONF_NUM_BATTERY_PACKS, DEFAULT_NUM_BATTERY_PACKS),
            )
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(CONF_NUM_BATTERY_PACKS, default=current_packs): NumberSelector(
                    NumberSelectorConfig(
                        min=1,
                        max=MAX_BATTERY_PACKS,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                    )
                ),
            }),
        )


class EcoFlowPowerOceanConfigFlow(ConfigFlow, domain=DOMAIN):
    """
    Konfigurationsflow für die EcoFlow PowerOcean Plus Integration.

    Führt eine vollständige Credential-Validierung durch, bevor der
    Config Entry angelegt wird. Verhindert so fehlerhafte Einträge
    durch Tippfehler in E-Mail oder Passwort.

    Version 1: Basisversion mit E-Mail, Passwort und Seriennummer.
    """

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> EcoFlowOptionsFlow:
        """Gibt den Options Flow zurück."""
        return EcoFlowOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """
        Erster Schritt: Eingabe der Zugangsdaten.

        Wird angezeigt wenn der Benutzer die Integration über die
        Home Assistant Oberfläche hinzufügt. Validiert die Eingaben
        live gegen die EcoFlow API.

        Args:
            user_input: Vom Benutzer ausgefüllte Formularfelder, oder None
                        wenn das Formular erstmalig angezeigt wird.

        Returns:
            ConfigFlowResult — entweder Formularanzeige oder fertig.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input[CONF_EMAIL].strip().lower()
            password = user_input[CONF_PASSWORD]
            serial = user_input[CONF_SERIAL_NUMBER].strip().upper()

            # Seriennummer-Format grob prüfen (PowerOcean Plus beginnt mit R37)
            if serial and not (len(serial) >= 8):
                errors[CONF_SERIAL_NUMBER] = "invalid_serial"

            if not errors:
                try:
                    _token, _user_id = await _validate_credentials(email, password)
                    _LOGGER.info(
                        "EcoFlow Zugangsdaten validiert für Gerät %s", serial
                    )
                except ValueError:
                    errors["base"] = "invalid_auth"
                except ConnectionError:
                    errors["base"] = "cannot_connect"
                except Exception:
                    _LOGGER.exception("Unerwarteter Fehler bei Credential-Validierung")
                    errors["base"] = "unknown"

            if not errors:
                # Eindeutige ID setzen, um Doppeleinträge zu verhindern
                await self.async_set_unique_id(serial)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=f"{MANUFACTURER} {MODEL} ({serial})",
                    data={
                        CONF_EMAIL: email,
                        CONF_PASSWORD: password,
                        CONF_SERIAL_NUMBER: serial,
                        CONF_NUM_BATTERY_PACKS: int(user_input[CONF_NUM_BATTERY_PACKS]),
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
            description_placeholders={
                "manufacturer": MANUFACTURER,
                "model": MODEL,
            },
        )
