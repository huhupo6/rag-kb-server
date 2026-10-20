import os
import re
import jieba
import pdfplumber
from docx.oxml.ns import qn
from docx import Document as DocxDocument
from langchain_text_splitters import RecursiveCharacterTextSplitter

# 清洗页眉页脚和多余空行
def clean_text(text):
    if not text:
        return ""

    # 过滤形如 "- 1 -" 或 "第 1 页" 的纯页码行
    text = re.sub(r'(?m)^\s*(-\s*\d+\s*-|第\s*\d+\s*页|Page\s*\d+)\s*$', '', text)

    # 把连续的空行压缩成一个换行符
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

# 提取 PDF 文本 + 表格 KV 转换
def extract_text_from_pdf(file_path):
    all_texts = []
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
    
            # 1. 提取普通文本并清洗
            page_text = page.extract_text()
            if page_text:
                all_texts.append(clean_text(page_text))

            # 2. 提取表格
            tables = page.extract_tables()
            for table in tables:
                if not table or len(table) < 2:
                    continue
                # 假设第一行是表头
                headers = table[0]

                # 遍历数据行，拼成 KV 文本
                for row in table[1:]:
                    # 过滤空行，并组合 "表头: 数据"
                    row_pairs = []
                    for i, cell in enumerate(row):
                        if i < len(headers) and cell and str(cell).strip():
                            row_pairs.append(f"{headers[i]}: {str(cell).strip()}")
                    
                    if row_pairs:
                        # 拼接成一句话
                        all_texts.append("【表格数据】" + "，".join(row_pairs))
                        
    return "\n\n".join(all_texts)

# 提取 DOCX 文本和表格
def extract_text_from_docx(file_path):
    doc = DocxDocument(file_path)
    all_texts = []
    # 用于去重，防止因复杂排版重复提取
    seen_texts = set()

    def add_text(text):
        cleaned = clean_text(text)
        if cleaned and cleaned not in seen_texts:
            seen_texts.add(cleaned)
            all_texts.append(cleaned)

    # 1. 提取普通段落文本
    for para in doc.paragraphs:
        add_text(para.text)

    # 2. 提取表格
    for table in doc.tables:
        if len(table.rows) < 2:
            continue

    # 获取表头
        headers = [cell.text.strip() for cell in table.rows[0].cells]
        
        # 遍历数据行
        for row in table.rows[1:]:
            row_pairs = []
            # 遍历该行的每一个单元格
            for i, cell in enumerate(row.cells):
                if i < len(headers) and cell.text.strip():
                    row_pairs.append(f"{headers[i]}: {cell.text.strip()}")
            
            if row_pairs:
                add_text("【表格数据】" + "，".join(row_pairs))

    # 3. 遍历 Word 底层 XML，提取文本框、图形里的文本
    for shape in doc.element.body.iter(qn('w:txbxContent')):
        for p in shape.iter(qn('w:p')):
            text = ''.join(node.text for node in p.iter(qn('w:t')) if node.text)
            add_text(text)
                
    return "\n\n".join(all_texts)

# 提取 TXT 文本
def extract_text_from_txt(file_path):
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        text = f.read()
    return clean_text(text)
    
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

# 使用 LangChain 的递归切分，保护语义完整
def split_into_chunks(text):
    if not text:
        return []
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=100,
        # 优先按段落换行切，其次按中文标点切
        separators=["\n\n", "\n", "。", "！", "？", "；", " ", ""]
    )
    return text_splitter.split_text(text)

# jiba 分词 避免数字与文字分开
def tokenize_chunk(text):
    pattern = r'\d+(?:\.\d+)?[%天个月元年号日]?'
    matches = re.findall(pattern, text)
    for m in matches:
        jieba.add_word(m)

    tokens = jieba.lcut(text)
    return ' '.join(tokens)