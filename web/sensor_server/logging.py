import json
import logging
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    """Structured JSON log formatter for file output."""

    def format(self, record):
        log_entry = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = self.formatException(record.exc_info)
        if hasattr(record, "request"):
            log_entry["request_method"] = getattr(record.request, "method", None)
            log_entry["request_path"] = getattr(record.request, "path", None)
        return json.dumps(log_entry, ensure_ascii=False)
