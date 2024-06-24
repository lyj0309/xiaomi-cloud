"""
Component to integrate with xiaomi cloud.

For more details about this component, please refer to
https://github.com/fineemb/xiaomi-cloud
"""
import logging
from homeassistant.core import Config, HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.components.device_tracker import (
    ATTR_BATTERY,
    DOMAIN as DEVICE_TRACKER,
)
from homeassistant.const import (
    CONF_PASSWORD,
    CONF_USERNAME,
    CONF_SCAN_INTERVAL
)

from .DataUpdateCoordinator import XiaomiCloudDataUpdateCoordinator

from .const import (
    DOMAIN,
    UNDO_UPDATE_LISTENER,
    COORDINATOR,
    CONF_COORDINATE_TYPE,
    CONF_COORDINATE_TYPE_BAIDU,
    CONF_COORDINATE_TYPE_ORIGINAL,
)

_LOGGER = logging.getLogger(__name__)

async def async_setup(hass: HomeAssistant, config: Config) -> bool:
    """Set up configured xiaomi cloud."""
    hass.data[DOMAIN] = {"devices": set(), "unsub_device_tracker": {}}
    return True

async def async_setup_entry(hass, config_entry) -> bool:
    """Set up xiaomi cloud as config entry."""
    username = config_entry.data[CONF_USERNAME]
    password = config_entry.data[CONF_PASSWORD]
    scan_interval = config_entry.options.get(CONF_SCAN_INTERVAL, 60)
    coordinate_type = config_entry.options.get(CONF_COORDINATE_TYPE, CONF_COORDINATE_TYPE_ORIGINAL)

    _LOGGER.debug("Username: %s", username)


    coordinator = XiaomiCloudDataUpdateCoordinator(
        hass, username, password, scan_interval, coordinate_type
    )
    await coordinator.async_refresh()

    if not coordinator.last_update_success:
        raise ConfigEntryNotReady

    undo_listener = config_entry.add_update_listener(update_listener)

    hass.data[DOMAIN][config_entry.entry_id] = {
        COORDINATOR: coordinator,
        UNDO_UPDATE_LISTENER: undo_listener,
    }
    hass.async_create_task(
        hass.config_entries.async_forward_entry_setup(config_entry, DEVICE_TRACKER)
    )

    async def services(call):
        """Handle the service call."""
        imei = call.data.get("imei")
        service = call.service
        if service == "noise":
            await coordinator._send_command({'service':'noise','data':{'imei':imei}})
        elif service == "find":
            await coordinator._send_command({'service':'find','data':{'imei':imei}})
        elif service == "lost":
            await coordinator._send_command({
                'service':'lost',
                'data':{
                    'imei':imei,
                    'content':call.data.get("content"),
                    'phone':call.data.get("phone"),
                    'onlinenotify':call.data.get("onlinenotify")
                    }})
        elif service == "clipboard":
            await coordinator._send_command({'service':'clipboard','data':{'text':call.data.get("text")}})

    hass.services.async_register(DOMAIN, "noise", services)
    hass.services.async_register(DOMAIN, "find", services)
    hass.services.async_register(DOMAIN, "lost", services)
    hass.services.async_register(DOMAIN, "clipboard", services)

    return True

async def async_unload_entry(hass, config_entry):
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_forward_entry_unload(config_entry, DEVICE_TRACKER)

    hass.data[DOMAIN][config_entry.entry_id][UNDO_UPDATE_LISTENER]()

    if unload_ok:
        hass.data[DOMAIN].pop(config_entry.entry_id)

    return unload_ok

async def update_listener(hass, config_entry):
    """Update listener."""
    await hass.config_entries.async_reload(config_entry.entry_id)

