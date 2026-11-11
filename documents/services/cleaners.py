# 3. 数据清洗

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

    # 将 HTML 表格转换为 Markdown 表格，保护语义
    def html_table_to_md(match):
        html_table = match.group(0)
        # 提取表头
        headers = re.findall(r'<th[^>]*>(.*?)</th>', html_table, re.IGNORECASE)
        if not headers:
            # 如果没 th，尝试提取第一行 td 当表头
            first_tr = re.search(r'<tr[^>]*>(.*?)</tr>', html_table, re.IGNORECASE)
            if first_tr:
                headers = re.findall(r'<td[^>]*>(.*?)</td>', first_tr.group(1), re.IGNORECASE)
        
        md_table = []
        if headers:
            md_table.append("| " + " | ".join([h.strip() for h in headers]) + " |")
            md_table.append("| " + " | ".join(["---"] * len(headers)) + " |")

        # 提取数据行
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html_table, re.IGNORECASE)
        # 如果第一行被当成表头了，就从第二行开始读
        start_idx = 1 if not re.search(r'<th', html_table, re.IGNORECASE) else 0
        
        for row in rows[start_idx:]:
            cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.IGNORECASE)
            if cells:
                md_table.append("| " + " | ".join([c.strip() for c in cells]) + " |")
        
        return "\n" + "\n".join(md_table) + "\n"

    # 匹配成对的 <table>...</table>
    text = re.sub(r'<table.*?>.*?</table>', html_table_to_md, text, flags=re.DOTALL | re.IGNORECASE)

    # 剩余的 <tr> <td> 标签直接去掉
    text = re.sub(r'<[^>]+>', '', text)

    return text.strip()
