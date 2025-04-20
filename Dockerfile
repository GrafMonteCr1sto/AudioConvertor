FROM python:3.9-slim

# Устанавливаем ffmpeg
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Устанавливаем рабочую директорию
WORKDIR /app

# Копируем зависимости и устанавливаем их
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код приложения
COPY . .

# Создаем директории для файлов
RUN mkdir -p uploads converted instance

# Открываем порт
EXPOSE 5000

# Запускаем приложение
CMD ["python", "app.py"] 