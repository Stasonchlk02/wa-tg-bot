from datetime import datetime


class PhoneRecord:
    def __init__(
        self,
        phone: str,
        file_name: str,
        sender_username: str,
        sender_first_name: str,
    ):
        self.phone = phone
        self.file_name = file_name
        self.sender_username = sender_username
        self.sender_first_name = sender_first_name
        self.timestamp = datetime.now()
        self.sent: bool = False  # Флаг отправки

    def to_dict(self) -> dict:
        return {
            "phone": self.phone,
            "file_name": self.file_name,
            "sender_username": self.sender_username,
            "sender_first_name": self.sender_first_name,
            "timestamp": self.timestamp.isoformat(),
            "sent": self.sent,
        }
