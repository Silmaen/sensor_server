from django.db import models


class SensorReading(models.Model):
    time = models.DateTimeField(primary_key=True)
    device_id = models.CharField(max_length=128)
    metric = models.CharField(max_length=64)
    value = models.FloatField()

    class Meta:
        # No Django auto-PK; managed via raw migration for hypertable compatibility
        managed = False
        db_table = "readings_sensorreading"
        ordering = ["-time"]

    def __str__(self):
        return f"{self.device_id}/{self.metric}={self.value} @ {self.time}"
