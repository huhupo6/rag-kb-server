# cleaners.py
# 功能：MinerU解析结果清洗模块，清洗脏文本、转换HTML表格为Markdown表格，给后续分块器使用
# 清洗目标：去除页码、图片标记、多余换行，统一表格格式，避免脏数据影响RAG检索

# Python内置
import re

# 数据清洗
def clean_markdown(text: str) -> str:
    """
    清洗 MinerU 输出的 Markdown 文本
    1. 移除页码行（ "- 1 -" / "第 1 页" / "Page 1"）
    2. 删除图片占位标记 ![]()，避免污染检索
    3. 压缩过多连续换行，保留段落分隔
    4. 将HTML表格转换为Markdown表格，保护跨页表格语义，防止表格被切碎
    5. 清除残留HTML标签
    :param text: MinerU解析后原始markdown文本
    :return: 清洗完成后的干净文本
    """
    if not text:
        return ""

    # 移除页码行，多行模式匹配整行页码
    text = re.sub(r'(?m)^\s*(-\s*\d+\s*-|第\s*\d+\s*页|Page\s*\d+)\s*$', '', text)
    # 移除图片占位标记 ![]()
    text = re.sub(r'!\[.*?\]\(.*?\)', '', text)
    # 连续3个及以上换行压缩为两个换行，保留段落分隔
    text = re.sub(r'\n{3,}', '\n\n', text)

    # 将 HTML 表格转换为 Markdown 表格，保护语义
    def html_table_to_md(match):
        """内嵌回调：正则匹配到table标签后，转为markdown表格"""
        html_table = match.group(0)
        # 提取表头 th
        headers = re.findall(r'<th[^>]*>(.*?)</th>', html_table, re.IGNORECASE)
        if not headers:
            # 如果没有 th，取第一行 td 作为表头
            first_tr = re.search(r'<tr[^>]*>(.*?)</tr>', html_table, re.IGNORECASE)
            if first_tr:
                headers = re.findall(r'<td[^>]*>(.*?)</td>', first_tr.group(1), re.IGNORECASE)

        md_table = []
        if headers:
            md_table.append("| " + " | ".join([h.strip() for h in headers]) + " |")
            md_table.append("| " + " | ".join(["---"] * len(headers)) + " |")

        # 提取所有数据行 tr
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html_table, re.IGNORECASE)
        # 无th时，第一行已经作为表头，遍历从第二行开始
        start_idx = 1 if not re.search(r'<th', html_table, re.IGNORECASE) else 0

        for row in rows[start_idx:]:
            cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.IGNORECASE)
            if cells:
                md_table.append("| " + " | ".join([c.strip() for c in cells]) + " |")

        return "\n" + "\n".join(md_table) + "\n"

    # 正则匹配完整 <table> 标签，调用回调转为markdown表格
    text = re.sub(r'<table.*?>.*?</table>', html_table_to_md, text, flags=re.DOTALL | re.IGNORECASE)

    # 清除剩下零散的HTML标签，如 <tr> <td> 等
    text = re.sub(r'<[^>]+>', '', text)

    return text.strip()
