import re
import pdfplumber
from typing import List

PHONE_PATTERN = re.compile(
    r'(\+7|8|7)[\s\-\(\)]*\d[\s\-\(\)]*\d[\s\-\(\)]*\d[\s\-\(\)]*\d[\s\-\(\)]*\d[\s\-\(\)]*\d[\s\-\(\)]*\d[\s\-\(\)]*\d[\s\-\(\)]*\d[\s\-\(\)]*\d'
)

def extract_phones_from_pdf(pdf_path: str) -> List[str]:
    phones = []

    with pdfplumber.open(pdf_path) as pdf:
        full_text = ""
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                full_text += text + "\n"

    matches = PHONE_PATTERN.findall(full_text)

    for match in matches:
        # Оставляем только цифры и + в начале
        cleaned = ''.join(ch for ch in match if ch.isdigit() or ch == '+')

        # Приводим к формату +7XXXXXXXXXX
        if cleaned.startswith('+7'):
            phone = cleaned[:12]
        elif cleaned.startswith('8'):
            phone = '+7' + cleaned[1:]
        elif cleaned.startswith('7'):
            phone = '+7' + cleaned[1:]
        else:
            phone = '+7' + cleaned

        if re.match(r'\+7\d{10}', phone) and phone not in phones:
            phones.append(phone)

    return phones