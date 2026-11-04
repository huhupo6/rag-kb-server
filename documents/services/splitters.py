import re
import jieba
from langchain_text_splitters import RecursiveCharacterTextSplitter


def split_into_chunks(text: str):
    """
    递归字符分块，适配中文语义切分
    """
    if not text:
        return []
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=100,
        separators=["\n\n", "\n", "。", "！", "？", "；", " ", ""]
    )
    return splitter.split_text(text)

def tokenize_chunk(text: str) -> str:
    """
    BM25 分词预处理
    把数字+单位（百分比、天、年等）加入词典，防止jieba切碎
    返回空格分隔的分词串，用于构建BM25索引
    """
    pattern = r'\d+(?:\.\d+)?[%天个月元年号日]?'
    matches = re.findall(pattern, text)
    for m in matches:
        jieba.add_word(m)

    tokens = jieba.lcut(text)
    return " ".join(tokens)
