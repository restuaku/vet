FROM python:3.11-slim

WORKDIR /app

# Copy requirements
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy aplikasi
COPY . .

# Run bot
CMD ["python", "bot.py"]
