from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from accounts.models import UserProfile


class LiveReadingsConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        user = self.scope.get("user")
        if user is None or not user.is_authenticated:
            await self.close()
            return

        # Check user is approved (superusers bypass)
        if not user.is_superuser:
            role = await self._get_role(user)
            if role is None:
                await self.close()
                return

        self.group_name = "live_readings"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def sensor_reading(self, event):
        """Handle sensor_reading messages from the channel layer."""
        await self.send_json(event["reading"])

    async def device_status(self, event):
        """Handle device_status messages from the channel layer."""
        await self.send_json(event["status"])

    @database_sync_to_async
    def _get_role(self, user):
        try:
            return user.profile.role
        except UserProfile.DoesNotExist:
            return None
