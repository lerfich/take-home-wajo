FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MAILWARD_CONTAINER=1

WORKDIR /app
COPY requirements-gmail.lock.txt ./
RUN python -m pip install --no-cache-dir -r requirements-gmail.lock.txt
COPY mail_agent ./mail_agent

EXPOSE 8765 8766
CMD ["python", "-m", "mail_agent.web", "--db", "data/web-groq.sqlite3", "--bind", "0.0.0.0"]
