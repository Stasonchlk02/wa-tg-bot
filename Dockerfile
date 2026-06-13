FROM python:3.11-slim

# Install Node.js 20 + git
RUN apt-get update && apt-get install -y \
    curl \
    bash \
    git \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Verify versions
RUN node --version && npm --version && python3 --version && git --version

WORKDIR /app

# Install Node dependencies
COPY package.json .
RUN npm install

# Install Python dependencies
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy all files
COPY . .

# Make start.sh executable
RUN chmod +x start.sh

CMD ["bash", "start.sh"]
