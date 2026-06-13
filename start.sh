#!/bin/bash
echo "=== Запуск ==="

# Запускаем Node
node wa_bridge.js &
WA_PID=$!
echo "WA Bridge PID: $WA_PID"
sleep 6

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

# Держим контейнер живым
wait -n $WA_PID $PY_PID
echo "Один из процессов упал"
kill $WA_PID $PY_PID 2>/dev/null
exit 1
