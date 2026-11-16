from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from rag_engine import hybrid_search
from llm_service import generate_stream, generate_sync
from bm25_engine import get_bm25_engine, clear_bm25_engine
import pymysql
import json
import redis
from config import DB_HOST, DB_USER, DB_PASSWORD, DB_NAME, REDIS_HOST, REDIS_PORT, REDIS_DB, redis_client

app = FastAPI(title="RAG 检索增强 API", version="1.0")

class ChatRequest(BaseModel):
    tenant_id: int
    user_id: int
    query: str

# 敏感词
SENSITIVE_KEYWORDS = ['薪资', '裁员', '赔偿', '竞业', '保密', '合同', '绩效', '开除']

# Pro 版 历史记录存储
def save_chat_to_db(request: ChatRequest, contexts: list, answer: str, usage: dict):
    try:
        cited_ids = [str(c['chunk_id']) for c in contexts]
        is_sensitive = any(kw in request.query for kw in SENSITIVE_KEYWORDS)
        
        conn = pymysql.connect(
            host=DB_HOST, user=DB_USER, password=DB_PASSWORD,
            database=DB_NAME, charset='utf8mb4'
        )
        with conn.cursor() as cursor:
            sql = """
                    INSERT INTO chat_history 
                    (tenant_id, user_id, question, answer, cited_chunks, is_sensitive, prompt_tokens, completion_tokens, total_tokens, created_at) 
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                """
            cursor.execute(sql, (
                request.tenant_id, request.user_id, request.query, answer,
                json.dumps(cited_ids), is_sensitive,
                usage.get('prompt_tokens', 0),
                usage.get('completion_tokens', 0),
                usage.get('total_tokens', 0)
            ))
            conn.commit()
            conn.close()
    except Exception as e:
        print(f"写入 MySQL 失败: {e}")

def stream_and_save(request: ChatRequest, contexts: list):
    full_answer = ""
    usage_data = {}

    for chunk in generate_stream(request.query, contexts):
        if chunk.startswith("__USAGE__"):
            usage_data = json.loads(chunk.replace("__USAGE__", ""))
            continue

        full_answer += chunk
        yield chunk

    if usage_data:
        total_tokens = usage_data.get('total_tokens', 0)
        if total_tokens > 0:
            redis_key = f"tenant_token_usage:{request.tenant_id}"
            pipe = redis_client.pipeline()
            pipe.incrby(redis_key, total_tokens)
            pipe.expire(redis_key, 86400)
            pipe.execute()

        # 保存到数据库
        save_chat_to_db(request, contexts, full_answer, usage_data)

# 混合检索 + 流式输出
@app.post("/api/chat")
async def chat_with_rag(request: ChatRequest):
    try:

        daily_limit = 500000
        redis_key = f"tenant_token_usage:{request.tenant_id}"
        used_tokens = int(redis_client.get(redis_key) or 0)

        if used_tokens >= daily_limit:
            raise HTTPException(status_code=429, detail="专业版今日 Pro 额度已耗尽，请明天再试")

        # 1. 执行混合检索 (BM25 + 向量 + RRF)
        contexts = hybrid_search(request.query, request.tenant_id, top_k=3)
        
        # 2. 返回 SSE 流式响应
        return StreamingResponse(
            generate_stream(request.query, contexts), 
            media_type="text/event-stream"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 供 Django 内部调用的清空缓存接口
@app.post("/api/refresh_cache")
def refresh_cache(tenant_id: int):
    try:
        clear_bm25_engine(tenant_id)
        return {"code": 200, "message": f"租户 {tenant_id} 的缓存已刷新"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 用于跑自动化测试的非流式接口
@app.post("/api/chat_test")
def chat_test(request: ChatRequest):
    try:
        contexts = hybrid_search(request.query, request.tenant_id, top_k=3)
        answer = generate_sync(request.query, contexts)
        return {
            "code": 200, 
            "answer": answer,
            "cited_chunks_count": len(contexts)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))