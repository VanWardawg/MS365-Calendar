"""Main calendar processing."""

import functools as ft
import logging
import re
from copy import deepcopy
from datetime import datetime, timedelta
from operator import attrgetter
from typing import Any, cast

from homeassistant.components.calendar import (
    EVENT_DESCRIPTION,
    EVENT_END,
    EVENT_RRULE,
    EVENT_START,
    EVENT_SUMMARY,
    CalendarEntity,
    CalendarEntityFeature,
    CalendarEvent,
    extract_offset,
    is_offset_reached,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_platform
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util
from requests.exceptions import HTTPError, RetryError

from .coordinator_integration import M365CalendarEventSyncManager, M365CalendarService, M365CalendarSyncCoordinator
from .store_integration import LocalCalendarStore, ScopedCalendarStore, InMemoryCalendarStore

from ..classes.config_entry import MS365ConfigEntry
from ..const import (
    CONF_ENABLE_UPDATE,
    CONF_ENTITY_NAME,
    EVENT_HA_EVENT,
)
from ..helpers.utils import clean_html
from .const_integration import (
    ATTR_ALL_DAY,
    ATTR_COLOR,
    ATTR_DATA,
    ATTR_EVENT_ID,
    ATTR_HEX_COLOR,
    ATTR_OFFSET,
    CONF_CAL_ID,
    CONF_DEVICE_ID,
    CONF_ENTITIES,
    CONF_EXCLUDE,
    CONF_HOURS_BACKWARD_TO_GET,
    CONF_HOURS_FORWARD_TO_GET,
    CONF_MAX_RESULTS,
    CONF_SEARCH,
    CONF_SENSITIVITY_EXCLUDE,
    CONF_TRACK,
    CONF_TRACK_NEW_CALENDAR,
    CONST_GROUP,
    DEFAULT_OFFSET,
    DOMAIN,
    DATA_STORE,
    EVENT_CREATE_CALENDAR_EVENT,
    EVENT_MODIFY_CALENDAR_EVENT,
    EVENT_MODIFY_CALENDAR_RECURRENCES,
    EVENT_REMOVE_CALENDAR_EVENT,
    EVENT_REMOVE_CALENDAR_RECURRENCES,
    EVENT_RESPOND_CALENDAR_EVENT,
    PERM_CALENDARS_READWRITE,
    YAML_CALENDARS_FILENAME,
    EventResponse,
)
from .filemgmt_integration import (
    async_update_calendar_file,
    build_yaml_file_path,
    build_yaml_filename,
    load_yaml_file,
)
from .schema_integration import (
    CALENDAR_SERVICE_CREATE_SCHEMA,
    CALENDAR_SERVICE_MODIFY_SCHEMA,
    CALENDAR_SERVICE_REMOVE_SCHEMA,
    CALENDAR_SERVICE_RESPOND_SCHEMA,
    YAML_CALENDAR_DEVICE_SCHEMA,
)
from .utils_integration import (
    add_call_data_to_event,
    build_calendar_entity_id,
    format_event_data,
    get_end_date,
    get_hass_date,
    get_start_date,
)

_LOGGER = logging.getLogger(__name__)


async def async_integration_setup_entry(
    hass: HomeAssistant,
    entry: MS365ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the MS365 platform."""

    update_supported = bool(
        entry.data[CONF_ENABLE_UPDATE]
        and entry.runtime_data.permissions.validate_authorization(
            PERM_CALENDARS_READWRITE
        )
    )
    calendars = await async_scan_for_calendars(hass, entry)
    await _async_setup_add_entities(
        hass,
        entry.runtime_data.ha_account.account,
        async_add_entities,
        entry,
        update_supported,
        calendars,
    )

    await _async_setup_register_services(update_supported)

    return True


async def _async_setup_add_entities(
    hass,
    account,
    async_add_entities,
    entry: MS365ConfigEntry,
    update_supported,
    calendar_edit_list,
):
    yaml_filename = build_yaml_filename(entry, YAML_CALENDARS_FILENAME)
    yaml_filepath = build_yaml_file_path(hass, yaml_filename)
    calendars = await hass.async_add_executor_job(
        load_yaml_file, yaml_filepath, CONF_CAL_ID, YAML_CALENDAR_DEVICE_SCHEMA
    )

    for cal_id, calendar in calendars.items():
        for entity in calendar.get(CONF_ENTITIES):
            if not entity[CONF_TRACK]:
                continue
            can_edit = next(
                (
                    calendar_edit.can_edit
                    for calendar_edit in calendar_edit_list
                    if calendar_edit.calendar_id == cal_id
                ),
                True,
            )
            entity_id = build_calendar_entity_id(
                entity.get(CONF_DEVICE_ID), entry.data[CONF_ENTITY_NAME]
            )

            update_calendar = update_supported and can_edit
            device_id = entity["device_id"]
            #store_key = f"{entity.get(CONF_NAME)}-{entry.entry_id}"
            store = InMemoryCalendarStore()
            try:
                api = M365CalendarService(
                    hass,
                    account,
                    cal_id,
                    entity.get(CONF_SENSITIVITY_EXCLUDE),
                    entity.get(CONF_SEARCH)
                )
                await api.async_calendar_init()
                unique_id = f"{entity.get(CONF_NAME)}"
                sync = M365CalendarEventSyncManager(
                    api,
                    cal_id,
                    store=ScopedCalendarStore(
                        store, unique_id
                    ),
                )
                coordinator = M365CalendarSyncCoordinator(
                    hass,
                    entry,
                    sync,
                    f"{entity.get(CONF_NAME)}",
                )
                cal = MS365CalendarEntity(
                    api,
                    coordinator,
                    cal_id,
                    entity,
                    entity_id,
                    device_id,
                    entry,
                    update_calendar,
                )
            except HTTPError:
                _LOGGER.warning(
                    "No permission for calendar, please remove - Name: %s; Device: %s;",
                    entity[CONF_NAME],
                    entity[CONF_DEVICE_ID],
                )
                continue

            async_add_entities([cal], True)
    return


async def _async_setup_register_services(update_supported):
    platform = entity_platform.async_get_current_platform()

    if update_supported:
        platform.async_register_entity_service(
            "create_calendar_event",
            CALENDAR_SERVICE_CREATE_SCHEMA,
            "async_create_calendar_event",
        )
        platform.async_register_entity_service(
            "modify_calendar_event",
            CALENDAR_SERVICE_MODIFY_SCHEMA,
            "async_modify_calendar_event",
        )
        platform.async_register_entity_service(
            "remove_calendar_event",
            CALENDAR_SERVICE_REMOVE_SCHEMA,
            "async_remove_calendar_event",
        )
        platform.async_register_entity_service(
            "respond_calendar_event",
            CALENDAR_SERVICE_RESPOND_SCHEMA,
            "async_respond_calendar_event",
        )


class MS365CalendarEntity(
    CoordinatorEntity[M365CalendarSyncCoordinator],
    CalendarEntity):
    """MS365 Calendar Event Processing."""

    _unrecorded_attributes = frozenset((ATTR_DATA, ATTR_COLOR, ATTR_HEX_COLOR))

    def __init__(
        self,
        api,
        coordinator,
        calendar_id,
        entity,
        entity_id,
        device_id,
        entry: MS365ConfigEntry,
        update_supported,
    ):
        """Initialise the MS365 Calendar Event."""
        super().__init__(coordinator)
        self.api = api
        self._entry = entry 
        self._start_offset = entity.get(CONF_HOURS_BACKWARD_TO_GET)
        self._end_offset = entity.get(CONF_HOURS_FORWARD_TO_GET)
        self._event = None
        self._name = f"{entity.get(CONF_NAME)}"
        self.entity_id = entity_id
        self._offset_reached = False
        self._data_attribute = []
        self._calendar_id = calendar_id
        self._device_id = device_id
        self._update_supported = update_supported
        if self._update_supported:
            self._attr_supported_features = (
                CalendarEntityFeature.CREATE_EVENT
                | CalendarEntityFeature.DELETE_EVENT
                | CalendarEntityFeature.UPDATE_EVENT
            )
        self._max_results = entity.get(CONF_MAX_RESULTS)
        self._error = None
        self.exclude = entity.get(CONF_EXCLUDE)

    @property
    def extra_state_attributes(self):
        """Extra state attributes."""
        attributes = {
            ATTR_DATA: self._data_attribute,
        }
        if hasattr(self.api.calendar, ATTR_COLOR):
            attributes[ATTR_COLOR] = self.api.calendar.color
        if hasattr(self.api.calendar, ATTR_HEX_COLOR) and self.api.calendar.hex_color:
            attributes[ATTR_HEX_COLOR] = self.api.calendar.hex_color
        if self._event:
            attributes[ATTR_ALL_DAY] = (
                self._event.all_day if self._event is not None else False
            )
            attributes[ATTR_OFFSET] = self._offset_reached
        return attributes

    @property
    def event(self):
        """Event property."""
        return self._event

    @property
    def name(self):
        """Name property."""
        return self._name

    @property
    def unique_id(self):
        """Entity unique id."""
        return f"{self._calendar_id}_{self._entry.data[CONF_ENTITY_NAME]}_{self._device_id}"
    
    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        await super().async_added_to_hass()

        # We do not ask for an update with async_add_entities()
        # because it will update disabled entities. This is started as a
        # task to let if sync in the background without blocking startup
        self.coordinator.config_entry.async_create_background_task(
            self.hass,
            self.coordinator.async_request_refresh(),
            "m365.calendar-refresh",
        )

    async def async_get_events(self, hass, start_date, end_date):
        """Get events."""
        _LOGGER.debug("Start get_events for %s", self.name)

        results = await self.coordinator.async_get_events(start_date, end_date)
        results = self._filter_events(results)
        results = self._sort_events(results)
        events = self._create_calendar_event_entities(results)

        _LOGGER.debug("End get_events for %s", self.name)
        return events
    
    def _create_calendar_event_entities(self, results):
        event_list = []
        for vevent in results:
            try:
                event_list.append(self._create_calendar_event_entity(vevent))
            except HomeAssistantError as err:
                _LOGGER.warning(
                    "Invalid event found - Error: %s, Event: %s", err, vevent
                )

        return event_list
    
    def _create_calendar_event_entity(self, vevent):
        event = CalendarEvent(
            get_hass_date(vevent.start, vevent.is_all_day),
            get_hass_date(get_end_date(vevent), vevent.is_all_day),
            vevent.subject,
            clean_html(vevent.body),
            vevent.location["displayName"],
            uid=vevent.object_id,
        )
        if vevent.series_master_id:
            event.recurrence_id = vevent.series_master_id
        return event
    
    def _filter_events(self, events):
        lst_events = list(events)
        if not events or not self.exclude:
            return lst_events

        rtn_events = []
        for event in lst_events:
            include = True
            for exclude in self.exclude:
                if re.search(exclude, event.subject):
                    include = False
            if include:
                rtn_events.append(event)

        return rtn_events

    def _sort_events(self, events):
        for event in events:
            event.start_sort = event.start
            if event.is_all_day:
                event.start_sort = dt_util.as_utc(
                    dt_util.start_of_local_day(event.start)
                )

        events.sort(key=attrgetter("start_sort"))

        return events

    async def async_update(self):
        """Do the update."""
        # Get today's event for HA Core.
        _LOGGER.debug("Start update for %s", self.name)

        range_start = dt_util.utcnow() + timedelta(hours=self._start_offset)
        range_end = dt_util.utcnow() + timedelta(hours=self._end_offset)
        self._build_extra_attributes(range_start, range_end)
        await self.coordinator.async_refresh()
        self._get_current_event()

        _LOGGER.debug("End update for %s", self.name)

    def _get_current_event(self,):
        vevent = self.coordinator.get_current_event()
        if vevent is None:
            _LOGGER.debug(
                "No matching event found in the calendar results for %s",
                self.entity_id,
            )
            event = None
            return
        try:
            event = self._create_calendar_event_entity(vevent)
            self._error = False
        except HomeAssistantError as err:
            if not self._error:
                _LOGGER.warning(
                    "Invalid event found - Error: %s, Event: %s", err, vevent
                )
                self._error = True
        event = deepcopy(event)

        if event:
            event.summary, offset = extract_offset(event.summary, DEFAULT_OFFSET)
            start = M365CalendarSyncCoordinator.to_datetime(event.start)
            self._offset_reached = is_offset_reached(start, offset)

        self._event = event

    async def _async_get_events_and_store(self, range_start, range_end):
        # Get events for extra attributes.
        try:
            start_of_day_utc = dt_util.as_utc(dt_util.start_of_local_day())
            start = min(start_of_day_utc, range_start)
            end = max(
                start_of_day_utc + timedelta(days=1),
                range_end,
            )
            await self.data.async_update_data(
                self.hass,
                start,
                end,
                self._max_results,
            )
        except (HTTPError, RetryError, ConnectionError) as err:
            self._log_error("Error getting calendar events for data", err)
            return

    def _build_extra_attributes(self, range_start, range_end):
        if self.coordinator.data is not None:
            self._data_attribute = []
            for event in self.coordinator.data:
                if event.end > range_start and event.start < range_end:
                    self._data_attribute.append(format_event_data(event))

    async def async_create_event(self, **kwargs: Any) -> None:
        """Add a new event to calendar."""
        start = kwargs[EVENT_START]
        end = kwargs[EVENT_END]
        is_all_day = not isinstance(start, datetime)
        subject = kwargs[EVENT_SUMMARY]
        body = kwargs.get(EVENT_DESCRIPTION)
        rrule = kwargs.get(EVENT_RRULE)
        try:
            await cast(
                M365CalendarSyncCoordinator, self.coordinator
            ).sync.store_service.async_add_event(subject, start, end, body=body, is_all_day=is_all_day, rrule=rrule)
        except HTTPError as err:
            raise HomeAssistantError(f"Error while creating event: {err!s}") from err
        await self.coordinator.async_refresh()

    async def async_update_event(
        self,
        uid: str,
        event: dict[str, Any],
        recurrence_id: str | None = None,
        recurrence_range: str | None = None,
    ) -> None:
        """Update an event on the calendar."""
        start = event[EVENT_START]
        end = event[EVENT_END]
        is_all_day = not isinstance(start, datetime)
        subject = event[EVENT_SUMMARY]
        body = event.get(EVENT_DESCRIPTION)
        rrule = event.get(EVENT_RRULE)
        await self.async_modify_calendar_event(
            event_id=uid,
            recurrence_id=recurrence_id,
            recurrence_range=recurrence_range,
            subject=subject,
            start=start,
            end=end,
            body=body,
            is_all_day=is_all_day,
            rrule=rrule,
        )

    async def async_delete_event(
        self,
        uid: str,
        recurrence_id: str | None = None,
        recurrence_range: str | None = None,
    ) -> None:
        """Delete an event on the calendar."""
        await self.async_remove_calendar_event(uid, recurrence_id, recurrence_range)

    async def async_create_calendar_event(self, subject, start, end, **kwargs):
        """Create the event."""

        self._validate_permissions()

        calendar = self.data.calendar

        event = calendar.new_event()
        event = add_call_data_to_event(event, subject, start, end, **kwargs)
        await self.hass.async_add_executor_job(event.save)
        self._raise_event(EVENT_CREATE_CALENDAR_EVENT, event.object_id)
        self.async_schedule_update_ha_state(True)

    async def async_modify_calendar_event(
        self,
        event_id,
        recurrence_id=None,
        recurrence_range=None,
        subject=None,
        start=None,
        end=None,
        **kwargs,
    ):
        """Modify the event."""

        self._validate_permissions()

        if self.api.group_calendar:
            _group_calendar_log(self.entity_id)

        if recurrence_range:
            await self._async_update_calendar_event(
                recurrence_id,
                EVENT_MODIFY_CALENDAR_RECURRENCES,
                subject,
                start,
                end,
                **kwargs,
            )
        else:
            await self._async_update_calendar_event(
                event_id, EVENT_MODIFY_CALENDAR_EVENT, subject, start, end, **kwargs
            )

    def _log_error(self, error, err):
        if not self._error:
            _LOGGER.warning("%s - %s", error, err)
            self._error = True
        else:
            _LOGGER.debug("Repeat error - %s - %s", error, err)

    async def _async_update_calendar_event(
        self, event_id, ha_event, subject, start, end, **kwargs
    ):
        await self.api.async_patch_event(event_id, subject, start, end, **kwargs)
        self._raise_event(ha_event, event_id)
        self.async_schedule_update_ha_state(True)

    async def async_remove_calendar_event(
        self,
        event_id,
        recurrence_id: str | None = None,
        recurrence_range: str | None = None,
    ):
        """Remove the event."""
        self._validate_permissions()

        if self.api.group_calendar:
            _group_calendar_log(self.entity_id)

        if recurrence_range:
            await self._async_delete_calendar_event(
                recurrence_id, EVENT_REMOVE_CALENDAR_RECURRENCES
            )
        else:
            await self._async_delete_calendar_event(
                event_id, EVENT_REMOVE_CALENDAR_EVENT
            )

    async def _async_delete_calendar_event(self, event_id, ha_event):
        await cast(
            M365CalendarSyncCoordinator, self.coordinator
        ).sync.store_service.async_delete_event(
            event_id
        )
        await self.coordinator.async_refresh()
        self._raise_event(ha_event, event_id)
        self.async_schedule_update_ha_state(True)

    async def async_respond_calendar_event(
        self, event_id, response, send_response=True, message=None
    ):
        """Respond to calendar event."""
        self._validate_permissions()

        if self.api.group_calendar:
            _group_calendar_log(self.entity_id)

        await self._async_send_response(event_id, response, send_response, message)
        self._raise_event(EVENT_RESPOND_CALENDAR_EVENT, event_id)
        self.async_schedule_update_ha_state(True)

    async def _async_send_response(self, event_id, response, send_response, message):
        event = await self.data.async_get_event(self.hass, event_id)
        if response == EventResponse.Accept:
            await self.hass.async_add_executor_job(
                ft.partial(event.accept_event, message, send_response=send_response)
            )

        elif response == EventResponse.Tentative:
            await self.hass.async_add_executor_job(
                ft.partial(
                    event.accept_event,
                    message,
                    tentatively=True,
                    send_response=send_response,
                )
            )

        elif response == EventResponse.Decline:
            await self.hass.async_add_executor_job(
                ft.partial(event.decline_event, message, send_response=send_response)
            )

    def _validate_permissions(self):
        if not self._entry.runtime_data.permissions.validate_authorization(
            PERM_CALENDARS_READWRITE
        ):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="not_authorised_to_event",
                translation_placeholders={
                    "calendar": self._name,
                    "error_message": PERM_CALENDARS_READWRITE,
                },
            )
        if not self._update_supported:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="calendar_not_editable",
                translation_placeholders={
                    "name": self._name,
                },
            )

        return True

    def _raise_event(self, event_type, event_id):
        self.hass.bus.fire(
            f"{DOMAIN}_{event_type}",
            {ATTR_EVENT_ID: event_id, EVENT_HA_EVENT: True},
        )
        _LOGGER.debug("%s - %s", event_type, event_id)
async def async_scan_for_calendars(hass, entry: MS365ConfigEntry):
    """Scan for new calendars."""

    schedule = await hass.async_add_executor_job(
        entry.runtime_data.ha_account.account.schedule
    )
    calendars = await hass.async_add_executor_job(schedule.list_calendars)
    track = entry.options.get(CONF_TRACK_NEW_CALENDAR, True)
    for calendar in calendars:
        await async_update_calendar_file(
            entry,
            calendar,
            hass,
            track,
        )
    return calendars


def _group_calendar_log(entity_id):
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="ms365_group_calendar_error",
        translation_placeholders={
            "entity_id": entity_id,
        },
    )
