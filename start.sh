#!/bin/bash

echo "=== Запуск WA-TG Bot ==="
echo "WA_BRIDGE_URL: ${WA_BRIDGE_URL:-http://localhost:3001}"
echo "WA_PHONE: ${WA_PHONE:-не задан}"

# Убиваем старые процессы
pkill -f "wa_bridge.js" 2>/dev/null
pkill -f "python3 main.py" 2>/dev/null
sleep 2

# Запускаем WA Bridge
echo "▶️ Запускаем WA Bridge..."
node wa_bridge.js &
WA_PID=$!
echo "WA Bridge PID: $WA_PID"

# Ждём пока WA Bridge поднимется (проверяем healthcheck)
echo "⏳ Ожидаем запуска WA Bridge..."
MAX_WAIT=30
WAITED=0
while [ $WAITED -lt $MAX_WAIT ]; do
    if curl -sf http://localhost:3001/status > /dev/null 2>&1; then
        echo "✅ WA Bridge готов (${WAITED}с)"
        break
    fi
    sleep 2
    WAITED=$((WAITED + 2))
done

if [ $WAITED -ge $MAX_WAIT ]; then
    echo "⚠️ WA Bridge не ответил за ${MAX_WAIT}с, но продолжаем..."
fi

# Проверяем что процесс жив
if ! kill -0 $WA_PID 2>/dev/null; then
    echo "❌ ОШИБКА: WA Bridge упал при старте"
    exit 1
fi

# Запускаем Python бот
echo "▶️ Запускаем Python бот..."
python3 main.py &
PY_PID=$!
echo "Python Bot PID: $PY_PID"
sleep 5

if ! kill -0 $PY_PID 2>/dev/null; then
    echo "❌ ОШИБКА: Python бот упал при старте"
    kill $WA_PID 2>/dev/null
    exit 1
fi

echo "=== ✅ Всё запущено ==="

# Мониторинг — перезапускаем если один из процессов упал
while true; do
    sleep 30
    
    if ! kill -0 $WA_PID 2>/dev/null; then
        echo "⚠️ WA Bridge упал, перезапускаем..."
        node wa_bridge.js &
        WA_PID=$!
        sleep 10
    fi
    
    if ! kill -0 $PY_PID 2>/dev/null; then
        echo "⚠️ Python бот упал, перезапускаем..."
        python3 main.py &
        PY_PID=$!
        sleep 5
    fi
done
