# 4. 数据切分
import re
import jieba
from langchain_text_splitters import RecursiveCharacterTextSplitter

# 递归字符分块，适配中文语义切分
def split_into_chunks(text: str):

    # 如果是空文本，直接返回空列表
    if not text:
        return []

    chunks = []
    # 使用正则匹配 Markdown 表格，同时记录位置
    # 匹配连续的以 | 开头的行
    table_pattern = re.compile(r'((?:^\|.*\|(?:\r?\n|$))+)', re.MULTILINE)
    
    last_pos = 0

    # 遍历所有找到的表格
    for match in table_pattern.finditer(text):
        # 1. 处理表格之前的普通文本（保持顺序）
        pre_text = text[last_pos:match.start()].strip()
        if pre_text:
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=400, 
                chunk_overlap=50,
                separators=["\n\n", "\n", "。", "！", "？", "；", " ", ""]
            )
            chunks.extend(splitter.split_text(pre_text))
        
        # 2. 处理表格本身（整块保护）
        table_text = match.group(1).strip()
        if len(table_text) > 1500:
            # 如果遇到极端超长表格，才强制按 1000 字切
            chunks.extend([table_text[i:i+1000] for i in range(0, len(table_text), 1000)])
        elif table_text:
            chunks.append(table_text)
            
        # 更新读取进度
        last_pos = match.end()
        
    # 3. 处理最后一个表格之后的剩余普通文本
    post_text = text[last_pos:].strip()
    if post_text:
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=400, 
            chunk_overlap=50,
            separators=["\n\n", "\n", "。", "！", "？", "；", " ", ""]
        )
        chunks.extend(splitter.split_text(post_text))
        
    return [c.strip() for c in chunks if c.strip()]

# jieba 分词预处理
def tokenize_chunk(text: str) -> str:
    # 正则：匹配数字，比如 3.14、100%、5天、3个月、2026年
    pattern = r'\d+(?:\.\d+)?[%天个月元年号日]?'
    matches = re.findall(pattern, text)
    for m in matches:
        # 动态把识别出来的数字/时间词条加到 jieba 词典
        jieba.add_word(m)

    # jieba.lcut：对全文做精确分词，返回词语列表
    tokens = jieba.lcut(text)
    # 使用空格把词语拼接成字符串，给 BM25Okapi 使用
    return " ".join(tokens)
