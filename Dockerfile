# Dockerfile
FROM python:3.11-slim

# Устанавливаем Node.js 20
RUN apt-get update && apt-get install -y \
    curl \
    bash \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Проверяем версии
RUN node --version && npm --version && python3 --version

WORKDIR /app

# Устанавливаем Node зависимости
COPY package.json .
RUN npm install

# Устанавливаем Python зависимости
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Копируем все файлы
COPY . .

# Делаем start.sh исполняемым
RUN chmod +x start.sh

CMD ["bash", "start.sh"]
