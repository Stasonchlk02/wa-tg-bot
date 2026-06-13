import json
import os
import logging
from datetime import datetime
from phone_record import PhoneRecord

logger = logging.getLogger(__name__)
DB_FILE = "phone_db.json"


class Database:
    """Простая JSON-база данных для хранения номеров"""

    def __init__(self):
        self.records: list[PhoneRecord] = []
        self.broadcast_text: str = ""  # Текст рассылки по умолчанию
        self.load()

    def load(self):
        """Загрузить данные из файла"""
        if not os.path.exists(DB_FILE):
            return
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.broadcast_text = data.get("broadcast_text", "")
                for item in data.get("records", []):
                    rec = PhoneRecord(
                        phone=item["phone"],
                        file_name=item.get("file_name", ""),
                        sender_username=item.get("sender_username", ""),
                        sender_first_name=item.get("sender_first_name", ""),
                    )
                    rec.sent = item.get("sent", False)
                    rec.timestamp = datetime.fromisoformat(
                        item.get("timestamp", datetime.now().isoformat())
                    )
                    self.records.append(rec)
            logger.info(f"Загружено {len(self.records)} записей из БД")
        except Exception as e:
            logger.error(f"Ошибка загрузки БД: {e}")

    def save(self):
        """Сохранить данные в файл"""
        try:
            data = {
                "broadcast_text": self.broadcast_text,
                "records": [r.to_dict() for r in self.records],
            }
            with open(DB_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения БД: {e}")

    def add_record(self, record: PhoneRecord) -> bool:
        """Добавить запись, вернуть True если новая"""
        existing_phones = {r.phone for r in self.records}
        if record.phone not in existing_phones:
            self.records.append(record)
            self.save()
            return True
        return False

    def add_phone_manual(self, phone: str, added_by: str = "admin") -> bool:
        """Ручное добавление номера"""
        rec = PhoneRecord(
            phone=phone,
            file_name="manual",
            sender_username=added_by,
            sender_first_name="Ручное добавление",
        )
        return self.add_record(rec)

    def get_all_phones(self) -> list[str]:
        """Все уникальные номера"""
        return list({r.phone for r in self.records})

    def get_unsent_phones(self) -> list[str]:
        """Номера, которым ещё не отправляли"""
        seen = set()
        result = []
        for r in self.records:
            if not r.sent and r.phone not in seen:
                seen.add(r.phone)
                result.append(r.phone)
        return result

    def mark_sent(self, phones: list[str]):
        """Пометить номера как отправленные"""
        phone_set = set(phones)
        for r in self.records:
            if r.phone in phone_set:
                r.sent = True
        self.save()

    def mark_unsent(self, phones: list[str]):
        """Сбросить статус отправки"""
        phone_set = set(phones)
        for r in self.records:
            if r.phone in phone_set:
                r.sent = False
        self.save()

    def delete_phone(self, phone: str) -> bool:
        """Удалить номер"""
        before = len(self.records)
        self.records = [r for r in self.records if r.phone != phone]
        if len(self.records) < before:
            self.save()
            return True
        return False

    def clear_all(self):
        """Очистить всё"""
        self.records = []
        self.save()

    def get_stats(self) -> dict:
        """Статистика"""
        all_phones = self.get_all_phones()
        unsent = self.get_unsent_phones()
        return {
            "total": len(all_phones),
            "sent": len(all_phones) - len(unsent),
            "unsent": len(unsent),
        }

    def set_broadcast_text(self, text: str):
        """Установить текст рассылки"""
        self.broadcast_text = text
        self.save()

    def get_records_page(self, page: int, per_page: int = 10) -> tuple[list, int]:
        """Получить страницу записей"""
        all_unique = {}
        for r in self.records:
            if r.phone not in all_unique:
                all_unique[r.phone] = r
        unique_list = list(all_unique.values())
        total_pages = max(1, (len(unique_list) + per_page - 1) // per_page)
        start = page * per_page
        end = start + per_page
        return unique_list[start:end], total_pages
