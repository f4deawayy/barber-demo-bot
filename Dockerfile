FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot.py .

# BOT_TOKEN и ADMIN_ID задаются в настройках хостинга (переменные окружения), не в файле
CMD ["python", "bot.py"]
