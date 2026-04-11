FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY route_admin_app.py .

ENV ROUTES_FILE=/etc/traefik/dynamic/100-workspaces.yml
EXPOSE 8080
CMD ["uvicorn", "route_admin_app:app", "--host", "0.0.0.0", "--port", "8080"]
