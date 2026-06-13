import requests
import logging
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

WA_BRIDGE_URL = os.environ.get("WA_BRIDGE_URL", "http://localhost:3001")


class WhatsAppSender:

    @classmethod
    def init(cls):
        """Проверяем подключение к WA Bridge"""
        try:
            resp = requests.get(f"{WA_BRIDGE_URL}/status", timeout=5)
            data = resp.json()
            logger.info(f"📱 WhatsApp Bridge статус: {data['status']}")

            if data.get('pairing_code'):
                logger.info(f"🔑 Pairing Code: {data['pairing_code']}")
                logger.info("Введите этот код в WhatsApp на телефоне!")

            if data['connected']:
                logger.info("✅ WhatsApp подключён и готов")
            else:
                logger.warning("⚠️ WhatsApp не подключён. Используйте /wapair в боте")

        except requests.exceptions.ConnectionError:
            logger.error("❌ WA Bridge не запущен! Запустите: node wa_bridge.js")
            raise
        except Exception as e:
            logger.error(f"❌ Ошибка подключения к WA Bridge: {e}")
            raise

    @classmethod
    def get_status(cls) -> dict:
        """Получить статус подключения"""
        try:
            resp = requests.get(f"{WA_BRIDGE_URL}/status", timeout=5)
            return resp.json()
        except Exception as e:
            return {"connected": False, "status": "bridge_offline", "error": str(e)}

    @classmethod
    def send_message(cls, phone: str, text: str) -> dict:
        """Отправить сообщение"""
        try:
            resp = requests.post(
                f"{WA_BRIDGE_URL}/send",
                json={"phone": phone, "message": text},
                timeout=30
            )
            result = resp.json()
            if result.get("success"):
                logger.info(f"✅ Отправлено → {phone}")
            else:
                logger.warning(f"❌ Не отправлено → {phone}: {result.get('error')}")
            return result
        except Exception as e:
            logger.error(f"❌ Ошибка связи с WA Bridge: {e}")
            return {"success": False, "error": str(e)}

    @classmethod
    def send_bulk(cls, phones: list, text: str, delay: int = 30) -> dict:
        """Массовая рассылка"""
        try:
            resp = requests.post(
                f"{WA_BRIDGE_URL}/send_bulk",
                json={"phones": phones, "message": text, "delay_seconds": delay},
                timeout=10
            )
            return resp.json()
        except Exception as e:
            return {"success": False, "error": str(e)}

    @classmethod
    def pair(cls, phone: str) -> dict:
        """Запросить pairing code"""
        try:
            resp = requests.post(
                f"{WA_BRIDGE_URL}/pair",
                json={"phone": phone},
                timeout=15
            )
            return resp.json()
        except Exception as e:
            return {"success": False, "error": str(e)}

    @classmethod
    def logout(cls) -> dict:
        """Выйти из WhatsApp"""
        try:
            resp = requests.post(f"{WA_BRIDGE_URL}/logout", timeout=10)
            return resp.json()
        except Exception as e:
            return {"success": False, "error": str(e)}

    @classmethod
    def shutdown(cls):
        """Ничего не нужно закрывать"""
        pass
