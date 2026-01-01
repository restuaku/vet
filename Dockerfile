FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

# Browser sudah terinstall di base image
RUN playwright install-deps

COPY . .
CMD ["python", "bot.py"]
