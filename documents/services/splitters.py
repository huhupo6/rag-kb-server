# 文档文本切分与分词预处理
# 职责：将解析完成的文档正文分块，保护跨页/长表格不被切碎；同时提供 BM25 所需分词预处理

import re
import jieba
from langchain_text_splitters import RecursiveCharacterTextSplitter


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
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=400,
                chunk_overlap=50,
                separators=["\n\n", "\n", "。", "！", "？", "；", " ", ""]
            )
            chunks.extend(splitter.split_text(pre_text))

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
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=400,
            chunk_overlap=50,
            separators=["\n\n", "\n", "。", "！", "？", "；", " ", ""]
        )
        chunks.extend(splitter.split_text(post_text))

    # 剔除空白块
    return [c.strip() for c in chunks if c.strip()]


def tokenize_chunk(text: str) -> str:
    """
    BM25 分词预处理
    - 识别数字 + 单位组合，动态加入 jieba 词典，避免被切碎（如：3天、50%、2026年）
    - 使用精确模式分词，最后用空格拼接，适配 rank_bm25 的输入格式
    :param text: 单个文本块
    :return: 空格分隔的分词字符串
    """
    # 匹配整数/小数，后可携带 %、天、个、月、年、元、号、日
    pattern = r'\d+(?:\.\d+)?[%天个月元年号日]?'
    matches = re.findall(pattern, text)

    for m in matches:
        # 将识别到的词条临时加入词典，保证分词完整性
        jieba.add_word(m)

    # 精确分词
    tokens = jieba.lcut(text)
    # 拼接为空格分隔字符串，供 BM25Okapi 使用
    return " ".join(tokens)
