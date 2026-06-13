import requests
import logging
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

WA_BRIDGE_URL = os.environ.get("WA_BRIDGE_URL", "http://localhost:3001")
WA_PHONE = os.environ.get("WA_PHONE", "")


class WhatsAppSender:

    @classmethod
    def _request(cls, method: str, endpoint: str, json=None) -> dict:
        url = f"{WA_BRIDGE_URL}{endpoint}"
        try:
            if method == "get":
                resp = requests.get(url, timeout=10)
            elif method == "post":
                resp = requests.post(url, json=json, timeout=60)
            else:
                return {"success": False, "error": "Unsupported method"}

            if resp.status_code == 200:
                data = resp.json()
                return {"success": True, **data}
            else:
                return {
                    "success": False,
                    "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
                }
        except requests.exceptions.ConnectionError:
            return {"success": False, "error": "WA Bridge недоступен"}
        except requests.exceptions.Timeout:
            return {"success": False, "error": "Таймаут запроса к WA Bridge"}
        except Exception as e:
            logger.exception("WA Bridge request failed")
            return {"success": False, "error": str(e)}

    @classmethod
    def get_status(cls) -> dict:
        return cls._request("get", "/status")

    @classmethod
    def send_message(cls, phone: str, text: str) -> dict:
        return cls._request("post", "/send", json={"phone": phone, "message": text})

    @classmethod
    def send_bulk(cls, phones: list, text: str, delay: int = 30) -> dict:
        return cls._request(
            "post",
            "/send_bulk",
            json={"phones": phones, "message": text, "delay": delay},
        )

    @classmethod
    def pair(cls, phone: str = "") -> dict:
        """
        Запросить pairing code.
        Если phone не передан — берём из переменной окружения WA_PHONE.
        """
        target_phone = phone or WA_PHONE
        if not target_phone:
            return {
                "success": False,
                "error": "Номер телефона не задан. Установите WA_PHONE в переменных окружения.",
            }
        return cls._request("post", "/pair", json={"phone": target_phone})

    @classmethod
    def logout(cls) -> dict:
        return cls._request("post", "/logout")
