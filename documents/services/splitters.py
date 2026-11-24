# 文档文本切分与分词预处理
# 职责：将解析完成的文档正文分块，保护跨页/长表格不被切碎；同时提供 BM25 所需分词预处理

import re
import jieba
from langchain_text_splitters import RecursiveCharacterTextSplitter
from kb_tokenizer import tokenize_text

# 将 Splitter 实例化（全局单例）
_splitter = RecursiveCharacterTextSplitter(
    chunk_size=400, 
    chunk_overlap=50,
    separators=["\n\n", "\n", "。", "！", "？", "；", " ", ""]
)

def split_into_chunks(text: str):
    """
    递归字符分块，带表格保护策略，适配中文文档
    - 将 Markdown 表格整体识别、保护，优先不拆分表格
    - 普通正文使用递归字符分割器做语义切分，携带重叠上下文
    - 如果表格极端超长，则强制截断，避免单个 chunk 过大
    :param text: 文档原始纯文本（Markdown）
    :return: list[str]，切分后的文本块列表
    """
    # 空文本直接返回空列表
    if not text:
        return []

    chunks = []
    # 正则匹配整块 Markdown 表格：以 | 开头的连续表格行
    table_pattern = re.compile(r'((?:^\|.*\|(?:\r?\n|$))+)', re.MULTILINE)

    last_pos = 0

    # 遍历所有匹配到的表格
    for match in table_pattern.finditer(text):
        # 1. 处理表格前面的普通正文
        pre_text = text[last_pos:match.start()].strip()
        if pre_text:
            # 直接复用全局 _splitter，不再每次新建
            chunks.extend(_splitter.split_text(pre_text))

        # 2. 处理表格本身，优先整块保留
        table_text = match.group(1).strip()
        if len(table_text) > 1500:
            # 超长表格兜底强制截断，防止单块过大
            chunks.extend([table_text[i:i + 1000] for i in range(0, len(table_text), 1000)])
        elif table_text:
            chunks.append(table_text)

        # 更新游标，继续向后扫描
        last_pos = match.end()

    # 3. 处理最后一张表格之后剩下的正文
    post_text = text[last_pos:].strip()
    if post_text:
        chunks.extend(_splitter.split_text(post_text))
        
    # 剔除空白块
    return [c.strip() for c in chunks if c.strip()]


def tokenize_chunk(text: str) -> str:
    """Django 入库端：调用公共包分词后，用空格拼接供 MySQL 使用"""
    tokens = tokenize_text(text)
    return " ".join(tokens)