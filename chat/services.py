import os
import re
import jieba

from django.db import connection
from django.db.models import Q

from openai import OpenAI
from documents.models import DocumentChunk
from dotenv import load_dotenv


load_dotenv()

# 初始化 DeepSeek 客户端
client = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com/v1"
)

# 敏感词库
SENSITIVE_KEYWORDS = ['薪资', '裁员', '赔偿', '竞业', '保密', '合同', '绩效', '开除']

# 对问题或文本进行 jieba 分词，保护数字和单位
def tokenize_text(text):
    pattern = r'\d+(?:\.\d+)?[%天个月元年号日]?'
    matches = re.findall(pattern, text)
    for m in matches:
        jieba.add_word(m)

    tokens = jieba.lcut(text)
    return ' '.join(tokens)

# 全文检索召回
def retrieve_chunks(question, tenant_id, top_k=3):
    query_tokenized = tokenize_text(question)
    # 给分词后的每个词加上 + 号
    boolean_query = " ".join([f"+{word}" for word in query_tokenized.split() if word.strip()])

    with connection.cursor() as cursor:
        sql = """
            SELECT id FROM document_chunks
            WHERE document_id IN (
                SELECT id FROM documents 
                WHERE tenant_id = %s AND is_current = 1 AND is_deleted = 0
            )
            AND MATCH(content_tokenized) AGAINST(%s IN BOOLEAN MODE)
            ORDER BY MATCH(content_tokenized) AGAINST(%s IN BOOLEAN MODE) DESC
            LIMIT %s
        """
        cursor.execute(sql, [tenant_id, boolean_query, boolean_query, top_k])
        chunk_ids = [row[0] for row in cursor.fetchall()]
    
    if not chunk_ids:
        return []
        
    chunks = list(DocumentChunk.objects.filter(id__in=chunk_ids).select_related('document'))
    
    chunk_dict = {c.id: c for c in chunks}
    sorted_chunks = [chunk_dict[cid] for cid in chunk_ids if cid in chunk_dict]
    
    return sorted_chunks

# 防幻觉约束的 Prompt
def build_prompt(question, chunks):
    context = ""
    for idx, chunk in enumerate(chunks):
        context += f"【资料{idx+1}】{chunk.content}\n\n"

    system_prompt = """你是一个严谨的企业知识库助手。
请严格遵守以下规则：
1. 仅根据【资料片段】回答用户问题。
2. 如果资料片段中没有明确的数字、日期或具体结论，请直接回复：“知识库中未找到明确规定，请联系企业管理员”。
3. 严禁根据外部常识推测或编造。"""

    user_prompt = f"【资料片段】\n{context}\n【用户问题】\n{question}"
    return system_prompt, user_prompt

# 调用大模型生成答案
def ask_llm(question, chunks):
    if not chunks:
        return "知识库中未找到相关内容，请联系管理员补充文档。"

    system_prompt, user_prompt = build_prompt(question, chunks)

    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.1,
            max_tokens=800
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"AI 服务暂时不可用，错误信息：{str(e)}"
