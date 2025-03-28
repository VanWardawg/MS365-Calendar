"""Calendar coordinator processing."""

import logging
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from O365.calendar import Event  # pylint: disable=no-name-in-module)

from .const_integration import (
    CONF_UPDATE_INTERVAL,
    DEFAULT_SYNC_EVENT_MAX_TIME,
    DEFAULT_SYNC_EVENT_MIN_TIME,
    DEFAULT_UPDATE_INTERVAL,
)
from .sync.sync import MS365CalendarEventSyncManager
from .sync.timeline import MS365Timeline
from .utils_integration import get_end_date, get_start_date

_LOGGER = logging.getLogger(__name__)
# Maximum number of upcoming events to consider for state changes between
# coordinator updates.
# MAX_UPCOMING_EVENTS = 20


class MS365CalendarSyncCoordinator(DataUpdateCoordinator):
    """Coordinator for calendar RPC calls that use an efficient sync."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        sync: MS365CalendarEventSyncManager,
        name: str,
    ) -> None:
        """Create the CalendarSyncUpdateCoordinator."""
        update_interval = entry.options.get(
            CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=name,
            update_interval=timedelta(seconds=update_interval),
        )
        self.sync = sync
        self._upcoming_timeline: MS365Timeline | None = None
        self.event = None
        self._sync_event_min_time = timedelta(days=DEFAULT_SYNC_EVENT_MIN_TIME)
        self._sync_event_max_time = timedelta(days=DEFAULT_SYNC_EVENT_MAX_TIME)

    async def _async_update_data(self) -> MS365Timeline:
        """Fetch data from API endpoint."""
        _LOGGER.debug("Started fetching %s data", self.name)
        try:
            await self.sync.run()
        except Exception as err:
            raise UpdateFailed(f"Error communicating with API: {err}") from err

        # _LOGGER.debug("Updating Data from API Endpoint %s", self.sync.calendar_id)
        timeline = await self.sync.store_service.async_get_timeline(
            dt_util.get_default_time_zone()
        )
        self._upcoming_timeline = timeline
        return timeline

    async def async_get_events(
        self, start_date: datetime, end_date: datetime
    ) -> Iterable[Event]:
        """Get all events in a specific time frame."""
        if not self.data:
            raise HomeAssistantError(
                "Unable to get events: Sync from server has not completed"
            )

        sync_start_time = dt_util.now() + self._sync_event_min_time
        sync_end_time = dt_util.now() + self._sync_event_max_time
        # If the request is for outside of the sync'ed data, manually request it now,
        # will not cache it though
        if end_date < sync_start_time or start_date > sync_end_time:
            events = await self.sync.async_list_events(start_date, end_date)

        else:
            events = self.data.overlapping(
                start_date,
                end_date,
            )

        return events

    def get_current_event(self):
        """Get the current event."""
        if not self.data:
            _LOGGER.debug(
                "No current event found for %s",
                self.sync.calendar_id,
            )
            self.event = None
            return
        today = datetime.now(timezone.utc)
        events = self.data.overlapping(
            today,
            today + timedelta(days=1),
        )

        started_event = None
        not_started_event = None
        all_day_event = None
        for event in events:
            if event.is_all_day:
                if not all_day_event and not self.is_finished(event):
                    all_day_event = event
                continue
            if self.is_started(event) and not self.is_finished(event):
                if not started_event:
                    started_event = event
                continue
            if (
                not self.is_finished(event)
                and not event.is_all_day
                and not not_started_event
            ):
                not_started_event = event

        vevent = None
        if started_event:
            vevent = started_event
        elif all_day_event:
            vevent = all_day_event
        elif not_started_event:
            vevent = not_started_event

        return vevent

    @staticmethod
    def is_started(vevent):
        """Is it over."""
        return dt_util.utcnow() >= MS365CalendarSyncCoordinator.to_datetime(
            get_start_date(vevent)
        )

    @staticmethod
    def is_finished(vevent):
        """Is it over."""
        return dt_util.utcnow() >= MS365CalendarSyncCoordinator.to_datetime(
            get_end_date(vevent)
        )

    @staticmethod
    def to_datetime(obj):
        """To datetime."""
        if not isinstance(obj, datetime):
            date_obj = dt_util.start_of_local_day(
                dt_util.dt.datetime.combine(obj, dt_util.dt.time.min)
            )
        else:
            date_obj = obj

        return dt_util.as_utc(date_obj)

    # @property
    # def upcoming(self) -> Iterable[Event] | None:
    #     """Return upcoming events if any."""
    #     if self._upcoming_timeline:
    #         return self._upcoming_timeline.active_after(dt_util.now())
    #     return None
