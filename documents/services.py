import os
import re
import jieba
import pdfplumber
from docx import Document as DocxDocument

# 提取 PDF 文本信息
def extract_text_from_pdf(file_path):
    texts = []
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                texts.append(page_text)
    return '\n'.join(texts)

# 提取 DOCX 文本
def extract_text_from_docx(file_path):
    doc = DocxDocument(file_path)
    return '\n'.join([p.text for p in doc.paragraphs if p.text])

# 提取 TXT 文本
def extract_text_from_txt(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        return f.read()

# 根据文件拓展名选择解析器
def extract_text(file_path):
    ext = os.path.splitext(file_path)[1].lower()
    if ext == '.pdf':
        return extract_text_from_pdf(file_path)
    elif ext == '.docx':
        return extract_text_from_docx(file_path)
    elif ext == '.txt':
        return extract_text_from_txt(file_path)
    else:
        raise ValueError(f'不支持的文件格式: {ext}')

    # 切分文本
def split_into_chunks(text, chunk_size=500, overlap=50):
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        if chunk.strip():
            chunks.append(chunk)
        start = end - overlap
    return chunks

# 避免数字与文字分开
def tokenize_chunk(text):
    pattern = r'\d+(?:\.\d+)?[%天个月元年号日时分秒]?'
    matches = re.findall(pattern, text)
    for m in matches:
        jieba.add_word(m)

    tokens = jieba.lcut(text)
    return ' '.join(tokens)