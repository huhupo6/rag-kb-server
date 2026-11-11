# 1. 文件分发

from .parsers import MinerUParser, TxtParser


def get_parser(suffix: str):
    """
    根据文件后缀分发解析器
    """
    suffix = suffix.lower()
    if suffix in (".pdf", ".docx", ".doc"):
        return MinerUParser()
    elif suffix == ".txt":
        return TxtParser()
    raise ValueError(f"暂不支持该文档格式: {suffix}")
