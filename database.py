import logging
import os
import psycopg2
import psycopg2.extras
from datetime import datetime
from phone_record import PhoneRecord

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")


class Database:
    def __init__(self):
        self.broadcast_text: str = ""
        self._init_db()
        self._load_broadcast_text()

    # ─── Подключение ───────────────────────────────────────────────────────────

    def _connect(self):
        return psycopg2.connect(DATABASE_URL)

    # ─── Инициализация таблиц ──────────────────────────────────────────────────

    def _init_db(self):
        """Создать таблицы если не существуют"""
        with self._connect() as conn:
            with conn.cursor() as cur:
                # Таблица номеров
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS phone_records (
                        id SERIAL PRIMARY KEY,
                        phone VARCHAR(20) UNIQUE NOT NULL,
                        file_name TEXT,
                        sender_username TEXT,
                        sender_first_name TEXT,
                        timestamp TIMESTAMP DEFAULT NOW(),
                        sent BOOLEAN DEFAULT FALSE
                    )
                """)
                # Таблица настроек
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS settings (
                        key TEXT PRIMARY KEY,
                        value TEXT
                    )
                """)
            conn.commit()
        logger.info("БД инициализирована")

    # ─── Настройки ─────────────────────────────────────────────────────────────

    def _load_broadcast_text(self):
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT value FROM settings WHERE key = 'broadcast_text'"
                    )
                    row = cur.fetchone()
                    self.broadcast_text = row[0] if row else ""
        except Exception as e:
            logger.error(f"Ошибка загрузки текста рассылки: {e}")
            self.broadcast_text = ""

    def set_broadcast_text(self, text: str):
        self.broadcast_text = text
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO settings (key, value)
                    VALUES ('broadcast_text', %s)
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                """, (text,))
            conn.commit()

    # ─── Записи ────────────────────────────────────────────────────────────────

    def add_record(self, record: PhoneRecord) -> bool:
        """Добавить запись. Вернуть True если новая."""
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO phone_records
                            (phone, file_name, sender_username, sender_first_name, timestamp, sent)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (phone) DO NOTHING
                    """, (
                        record.phone,
                        record.file_name,
                        record.sender_username,
                        record.sender_first_name,
                        record.timestamp,
                        record.sent,
                    ))
                    inserted = cur.rowcount
                conn.commit()
            return inserted > 0
        except Exception as e:
            logger.error(f"Ошибка добавления записи: {e}")
            return False

    def add_phone_manual(self, phone: str, added_by: str = "admin") -> bool:
        rec = PhoneRecord(
            phone=phone,
            file_name="manual",
            sender_username=added_by,
            sender_first_name="Ручное добавление",
        )
        return self.add_record(rec)

    def get_all_phones(self) -> list[str]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT phone FROM phone_records")
                return [row[0] for row in cur.fetchall()]

    def get_unsent_phones(self) -> list[str]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT phone FROM phone_records WHERE sent = FALSE"
                )
                return [row[0] for row in cur.fetchall()]

    def mark_sent(self, phones: list[str]):
        if not phones:
            return
        with self._connect() as conn:
            with conn.cursor() as cur:
                psycopg2.extras.execute_batch(
                    cur,
                    "UPDATE phone_records SET sent = TRUE WHERE phone = %s",
                    [(p,) for p in phones],
                )
            conn.commit()

    def mark_unsent(self, phones: list[str]):
        if not phones:
            return
        with self._connect() as conn:
            with conn.cursor() as cur:
                psycopg2.extras.execute_batch(
                    cur,
                    "UPDATE phone_records SET sent = FALSE WHERE phone = %s",
                    [(p,) for p in phones],
                )
            conn.commit()

    def delete_phone(self, phone: str) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM phone_records WHERE phone = %s", (phone,)
                )
                deleted = cur.rowcount
            conn.commit()
        return deleted > 0

    def clear_all(self):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM phone_records")
            conn.commit()

    def get_stats(self) -> dict:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM phone_records")
                total = cur.fetchone()[0]
                cur.execute(
                    "SELECT COUNT(*) FROM phone_records WHERE sent = TRUE"
                )
                sent = cur.fetchone()[0]
        return {
            "total": total,
            "sent": sent,
            "unsent": total - sent,
        }

    def get_records_page(self, page: int, per_page: int = 10):
        """Получить страницу записей"""
        offset = page * per_page
        with self._connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM phone_records"
                )
                total = cur.fetchone()[0]
                total_pages = max(1, (total + per_page - 1) // per_page)

                cur.execute("""
                    SELECT phone, file_name, sender_username,
                           sender_first_name, timestamp, sent
                    FROM phone_records
                    ORDER BY timestamp DESC
                    LIMIT %s OFFSET %s
                """, (per_page, offset))

                rows = cur.fetchall()

        # Преобразуем в PhoneRecord
        records = []
        for row in rows:
            rec = PhoneRecord(
                phone=row["phone"],
                file_name=row["file_name"] or "",
                sender_username=row["sender_username"] or "",
                sender_first_name=row["sender_first_name"] or "",
            )
            rec.sent = row["sent"]
            rec.timestamp = row["timestamp"]
            records.append(rec)

        return records, total_pages
