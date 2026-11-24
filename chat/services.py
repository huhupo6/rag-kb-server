import os
import re

from django.db import connection

from openai import OpenAI
from documents.models import DocumentChunk
from dotenv import load_dotenv
from kb_tokenizer import tokenize_text

# 加载环境变量
load_dotenv()

# 初始化 DeepSeek 大模型客户端，复用统一配置
client = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com/v1"
)

# 敏感词库，用于检测用户提问是否包含敏感内容
SENSITIVE_KEYWORDS = ['薪资', '裁员', '赔偿', '竞业', '保密', '合同', '绩效', '开除']

def retrieve_chunks(question: str, tenant_id: int, top_k: int = 3) -> list[DocumentChunk]:
    """
    MySQL全文检索召回文档块（Boolean模式）
    只召回当前租户、有效未删除文档的chunk，按全文匹配分数排序
    :param question: 用户原始问题
    :param tenant_id: 租户ID，行级隔离
    :param top_k: 召回片段数量
    :return: 按相关性排序好的DocumentChunk对象列表
    """
    query_tokens = tokenize_text(question)
    # 构造布尔查询，要求所有分词词项都命中
    boolean_query = " ".join([f"+{word}" for word in query_tokens if word.strip()])

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

    # 批量查询chunk并保持MySQL返回的相关性顺序
    chunks = list(DocumentChunk.objects.filter(id__in=chunk_ids).select_related('document'))
    chunk_dict = {c.id: c for c in chunks}
    sorted_chunks = [chunk_dict[cid] for cid in chunk_ids if cid in chunk_dict]

    return sorted_chunks


def build_prompt(question: str, chunks: list[DocumentChunk]) -> tuple[str, str]:
    """
    构造RAG提示词，把召回的文档片段作为上下文注入
    :param question: 用户提问
    :param chunks: 召回的文档片段列表
    :return: system_prompt, user_prompt
    """
    context_text = ""
    for idx, chunk in enumerate(chunks):
        # 拼入文档名和块序号
        doc_name = chunk.document.file_name if hasattr(chunk, 'document') else "未知文档"
        context_text += f"【资料{idx+1}】(来自文件: {doc_name})\n{chunk.content}\n\n"

    system_prompt = """你是一个严谨的企业知识库助手。
请严格遵守以下规则：
1. 仅根据【资料片段】回答用户问题。
2. 在回答的每一句话末尾，必须使用 `[资料1]`、`[资料2]` 格式标注你的引用来源（X为资料序号，例如[资料1]）。
3. 如果资料片段中没有明确的数字、日期或具体结论，请直接回复：“知识库中未找到明确规定，请联系企业管理员”。
4. 严禁根据外部常识推测或编造。"""

    user_prompt = f"【资料片段】\n{context_text}\n【用户问题】\n{question}"
    return system_prompt, user_prompt

def verify_citations(answer: str, chunks: list[DocumentChunk]) -> str:
    
    if not chunks:
        return answer
        
    # 找出回答中所有 [资料X]
    cited_matches = re.findall(r'\[资料(.*?)\]', answer)
    max_valid_idx = len(chunks)
    
    for match in cited_matches:
        # 如果模型输出的是 [资料X]，或者无法转为数字，或者超出范围
        if not match.isdigit() or int(match) < 1 or int(match) > max_valid_idx:
            answer = answer.replace(f"[资料{match}]", "[引用异常]")
            
    return answer

def ask_llm(question: str, chunks: list[DocumentChunk]) -> tuple[str, dict]:
    """
    调用DeepSeek大模型生成回答，统计token消耗
    :param question: 用户问题
    :param chunks: 召回上下文片段
    :return: 正常返回 (answer, usage_data)；异常返回错误字符串
    """
    if not chunks:
        return "知识库中未找到相关内容，请联系管理员补充文档。", {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0
        }

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

        answer = response.choices[0].message.content

        answer = verify_citations(answer, chunks)

        # 封装token用量，用于写入chat_history记录表
        usage_data = {
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
        }

        return answer, usage_data

    except Exception as e:
        return f"AI 服务暂时不可用，错误信息：{str(e)}", {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0
        }
