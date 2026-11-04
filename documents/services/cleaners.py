# 数据清洗
import re

def clean_markdown(text: str) -> str:
    """
    清洗 MinerU 输出的 Markdown 文本
    1. 移除页码行（ "- 1 -" / "第 1 页" / "Page 1"）
    2. 删除图片占位标记 ![]()，避免污染检索
    3. 压缩过多连续换行
    """
    if not text:
        return ""

    # 移除页码行
    text = re.sub(r'(?m)^\s*(-\s*\d+\s*-|第\s*\d+\s*页|Page\s*\d+)\s*$', '', text)
    # 移除图片占位标记
    text = re.sub(r'!\[.*?\]\(.*?\)', '', text)
    # 连续3个及以上换行压缩为两个换行，保留段落分隔
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()
