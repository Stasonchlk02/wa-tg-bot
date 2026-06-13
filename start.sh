#!/bin/bash
echo "=== Запуск ==="

# Убиваем старые процессы если есть
pkill -f "wa_bridge.js" 2>/dev/null
pkill -f "python3 main.py" 2>/dev/null
sleep 2

# Запускаем Node
node wa_bridge.js &
WA_PID=$!
echo "WA Bridge PID: $WA_PID"
sleep 8

# Проверяем жив ли node
if ! kill -0 $WA_PID 2>/dev/null; then
    echo "ОШИБКА: WA Bridge упал"
    exit 1
fi

# Запускаем Python
python3 main.py &
PY_PID=$!
echo "Python Bot PID: $PY_PID"
sleep 3

if ! kill -0 $PY_PID 2>/dev/null; then
    echo "ОШИБКА: Python бот упал"
    kill $WA_PID 2>/dev/null
    exit 1
fi

echo "=== Всё запущено ==="

wait -n $WA_PID $PY_PID
echo "Один из процессов упал"
kill $WA_PID $PY_PID 2>/dev/null
exit 1
