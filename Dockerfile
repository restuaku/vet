# MATCH dengan requirements.txt version
FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

WORKDIR /app

RUN apt-get update && apt-get install -y \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Verify browser binary exists
RUN ls -la /ms-playwright/chromium-*/chrome-linux/chrome || echo "Browser check"

COPY . .

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

CMD ["python", "bot.py"]
