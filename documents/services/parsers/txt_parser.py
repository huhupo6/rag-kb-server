from .base import BaseParser

class TxtParser(BaseParser):
    """纯文本txt解析器"""
    def parse(self, file_path: str) -> str:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
        return text
