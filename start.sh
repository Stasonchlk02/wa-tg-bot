# start.sh
#!/bin/bash
set -e

echo "========================================="
echo "🚀 Запуск WA-TG Bot"
echo "========================================="

# Запускаем WhatsApp Bridge в фоне
echo "📱 Запускаю WhatsApp Bridge на порту ${WA_PORT:-3001}..."
node wa_bridge.js &
WA_PID=$!

# Ждём пока Node поднимется
echo "⏳ Жду запуска WA Bridge..."
sleep 8

# Проверяем что bridge жив
if ! kill -0 $WA_PID 2>/dev/null; then
    echo "❌ WA Bridge не запустился!"
    exit 1
fi

echo "✅ WA Bridge запущен (PID: $WA_PID)"

# Запускаем Telegram бота
echo "🤖 Запускаю Telegram бота..."
python3 main.py &
PY_PID=$!

sleep 3

if ! kill -0 $PY_PID 2>/dev/null; then
    echo "❌ Telegram бот не запустился!"
    kill $WA_PID 2>/dev/null
    exit 1
fi

echo "========================================="
echo "✅ Всё запущено!"
echo "   WA Bridge PID: $WA_PID"
echo "   TG Bot PID:    $PY_PID"
echo "========================================="

# Ждём завершения любого процесса
wait -n $WA_PID $PY_PID
EXIT_CODE=$?

echo "❌ Один из процессов упал (код: $EXIT_CODE)"
kill $WA_PID $PY_PID 2>/dev/null
exit 1