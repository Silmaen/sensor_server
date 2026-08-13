# 3.13 is the highest Python supported by Django 5.2 (requirements pin django<6);
# 3.14 needs Django 6.0, which is a framework upgrade, not an image bump.
FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    supervisor gettext \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
CMD ["supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
