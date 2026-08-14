FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x /srv/docker-entrypoint.sh

EXPOSE 80

# El entrypoint deja la base al día antes de arrancar; el comando decide si
# este contenedor es la API o el worker (misma imagen, dos servicios).
ENTRYPOINT ["/srv/docker-entrypoint.sh"]

# EasyPanel inyecta PORT; default 80 (convención NODO).
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-80}"]
