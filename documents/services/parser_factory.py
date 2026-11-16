# parser_factory.py
# 功能：解析器工厂，工厂模式，根据文件后缀分发不同文档解析器
# 支持格式：pdf / docx / doc（MinerU解析）、txt（简易文本解析）

# 当前app自定义模块
from .parsers import MinerUParser, TxtParser

def get_parser(suffix: str):
    """
    根据文件后缀分发对应的文档解析器
    支持：pdf / docx / doc 使用MinerU解析器（支持跨页表格识别合并）；txt使用简易文本解析器
    :param suffix: 文件后缀（如 .pdf、.txt）
    :return: 解析器实例
    """
    suffix = suffix.lower()
    if suffix in (".pdf", ".docx", ".doc"):
        return MinerUParser()
    elif suffix == ".txt":
        return TxtParser()
    raise ValueError(f"暂不支持该文档格式: {suffix}")
