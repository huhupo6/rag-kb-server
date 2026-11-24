# 1. Python 内置
import json
import datetime
import traceback
import asyncio
from typing import Generator, AsyncGenerator, Optional

# 2. 第三方
import logging
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import pymysql
from pymysql.err import OperationalError

# 3. 项目自定义模块
from rag_engine import hybrid_search
from llm_service import generate_stream, generate_sync
from bm25_engine import clear_bm25_engine
from config import DB_HOST, DB_USER, DB_PASSWORD, DB_NAME, redis_client

logger = logging.getLogger(__name__)
app = FastAPI(title="RAG 检索增强 API", version="1.0")

SENSITIVE_KEYWORDS = ['薪资', '裁员', '赔偿', '竞业', '保密', '合同', '绩效', '开除']
DAILY_TOKEN_LIMIT = 500000
REDIS_TTL_SECONDS = 86400
TOP_K = 3

class ChatRequest(BaseModel):
    """聊天请求入参模型
    :param tenant_id: 租户ID，多租户隔离标识
    :param user_id: 当前提问用户ID
    :param query: 用户提问文本
    """
    tenant_id: int
    user_id: int
    query: str

def get_db_conn():
    """获取数据库连接上下文"""
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        charset='utf8mb4'
    )

def save_chat_to_db(request: ChatRequest, contexts: list, answer: str, usage: dict):
    """
    将问答记录、引用片段、token消耗写入聊天历史表，用于审计、溯源、用量统计
    :param request: 前端聊天请求对象
    :param contexts: 检索命中的文档块列表
    :param answer: 大模型返回的回答文本
    :param usage: token消耗统计字典（prompt/completion/total）
    """
    conn: Optional[pymysql.connections.Connection] = None
    try:
        # 提取引用的chunk_id，转为json字符串存入数据库
        cited_ids = [str(c['chunk_id']) for c in contexts]
        # 简单敏感词检测：问题中包含敏感词则标记
        is_sensitive = any(kw in request.query for kw in SENSITIVE_KEYWORDS)

        conn = get_db_conn()
        with conn.cursor() as cursor:
            sql = """
                INSERT INTO chat_history
                (tenant_id, user_id, question, answer, cited_chunks, is_sensitive, prompt_tokens, completion_tokens, total_tokens, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            """
            cursor.execute(sql, (
                request.tenant_id,
                request.user_id,
                request.query,
                answer,
                json.dumps(cited_ids, ensure_ascii=False),
                is_sensitive,
                usage.get('prompt_tokens', 0),
                usage.get('completion_tokens', 0),
                usage.get('total_tokens', 0),
            ))
            conn.commit()
            logger.info(f"chat record saved, tenant={request.tenant_id}, user={request.user_id}")
    except Exception as e:
       logger.error(f"写入聊天记录数据库异常: {e}", exc_info=True)

    finally:
        if conn:
            conn.close()

def incr_tenant_token_usage(tenant_id: int, total_tokens: int):
    """Redis原子累加租户当日Token用量，统一封装"""
    if total_tokens <= 0:
        return
    today_str = datetime.datetime.now().strftime('%Y%m%d')
    redis_key = f"tenant_token_usage:{tenant_id}:{today_str}"
    pipe = redis_client.pipeline()
    pipe.incrby(redis_key, total_tokens)
    pipe.expire(redis_key, REDIS_TTL_SECONDS, nx=True)
    pipe.execute()

def stream_and_save(request: ChatRequest, contexts: list) -> Generator[str, None, None]:
    """
    流式生成答案的同步生成器。
    说明：FastAPI 会自动将同步生成器放入线程池执行，不会阻塞主事件循环。
    通过捕获 GeneratorExit 感知客户端断开，实现熔断。
    """
    full_answer = ""
    usage_data = {}
    response_stream = generate_stream(request.query, contexts)

    try:
        for chunk in response_stream:
            if chunk.startswith("__USAGE__"):
                usage_data = json.loads(chunk.replace("__USAGE__", ""))
                continue

            full_answer += chunk
            yield chunk

    except GeneratorExit:
        # 捕获客户端断连
        logger.warning("客户端断开连接 (GeneratorExit)，立即熔断 LLM 请求")
        raise

    except Exception as e:
        logger.error(f"LLM生成流式异常: {e}", exc_info=True)
        yield f"\n[生成中断: {str(e)}]"
    finally:
        if hasattr(response_stream, 'close'):
            response_stream.close()

    if usage_data:
        total_tokens = usage_data.get("total_tokens", 0)
        incr_tenant_token_usage(request.tenant_id, total_tokens)
        save_chat_to_db(request, contexts, full_answer, usage_data)

@app.post("/api/chat")
async def chat_with_rag(request: ChatRequest):
    """
    RAG流式问答主接口（SSE流式输出）
    """
    try:
        today_str = datetime.datetime.now().strftime('%Y%m%d')
        redis_key = f"tenant_token_usage:{request.tenant_id}:{today_str}"
        used_tokens = int(redis_client.get(redis_key) or 0)

        if used_tokens >= DAILY_TOKEN_LIMIT:
            raise HTTPException(status_code=429, detail="专业版今日 Pro 额度已耗尽，请明天再试")

        contexts = hybrid_search(request.query, request.tenant_id, top_k=TOP_K)
        if not isinstance(contexts, list):
            contexts = []

        return StreamingResponse(
            stream_and_save(request, contexts),
            media_type="text/event-stream"
        )
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error("/api/chat 全局异常", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/refresh_cache")
def refresh_cache(tenant_id: int):
    """
    缓存刷新接口，供Django服务在文档新增/修改/删除时调用
    作用：递增Redis版本号，通知所有worker在下一次检索时重建BM25索引
    :param tenant_id: 需要刷新缓存的租户ID
    """
    try:
        clear_bm25_engine(tenant_id)
        logger.info(f"refresh bm25 cache success, tenant_id={tenant_id}")
        return {"code": 200, "message": f"租户 {tenant_id} 的缓存已刷新"}
    except Exception as e:
        logger.error("/api/refresh_cache 异常", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/chat_test")
def chat_test(request: ChatRequest):
    """
    非流式问答测试接口，用于自动化测试、调试
    流程：额度校验 → 混合检索 → 同步调用LLM → Redis用量统计 + 问答落库 → 返回完整结果
    :param request: 聊天入参
    :return: 包含答案、引用数量、token消耗的json结果
    """
    try:
        today_str = datetime.datetime.now().strftime('%Y%m%d')
        redis_key = f"tenant_token_usage:{request.tenant_id}:{today_str}"
        used_tokens = int(redis_client.get(redis_key) or 0)

        if used_tokens >= DAILY_TOKEN_LIMIT:
            raise HTTPException(status_code=429, detail="专业版今日 Pro 额度已耗尽，请明天再试")

        contexts = hybrid_search(request.query, request.tenant_id, top_k=TOP_K)
        if not isinstance(contexts, list):
            contexts = []

        answer, usage_data = generate_sync(request.query, contexts)
        total_tokens = usage_data.get("total_tokens", 0)
        incr_tenant_token_usage(request.tenant_id, total_tokens)
        save_chat_to_db(request, contexts, answer, usage_data)

        return {
            "code": 200,
            "answer": answer,
            "cited_chunks_count": len(contexts),
            "tokens_used": total_tokens
        }
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error("/api/chat_test 异常", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))