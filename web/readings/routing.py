from django.urls import path

from . import consumers

websocket_urlpatterns = [
    path("ws/live/", consumers.LiveReadingsConsumer.as_asgi()),
]
