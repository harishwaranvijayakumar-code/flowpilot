
#!/bin/sh
set -e

echo "Applying database migrations..."
python manage.py migrate --noinput

echo "Starting FlowPilot..."
exec gunicorn config.wsgi:application --bind "0.0.0.0:${PORT:-10000}"
