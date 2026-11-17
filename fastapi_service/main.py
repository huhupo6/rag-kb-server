# 1. Python 内置
import json
import datetime
import traceback

# 2. 第三方
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import pymysql

# 3. 项目自定义模块
from rag_engine import hybrid_search
from llm_service import generate_stream, generate_sync
from bm25_engine import clear_bm25_engine
from config import DB_HOST, DB_USER, DB_PASSWORD, DB_NAME, redis_client

app = FastAPI(title="RAG 检索增强 API", version="1.0")


class ChatRequest(BaseModel):
    """聊天请求入参模型
    :param tenant_id: 租户ID，多租户隔离标识
    :param user_id: 当前提问用户ID
    :param query: 用户提问文本
    """
    tenant_id: int
    user_id: int
    query: str


# 敏感关键词列表，用于简单内容风险标记
SENSITIVE_KEYWORDS = ['薪资', '裁员', '赔偿', '竞业', '保密', '合同', '绩效', '开除']


def save_chat_to_db(request: ChatRequest, contexts: list, answer: str, usage: dict):
    """
    将问答记录、引用片段、token消耗写入聊天历史表，用于审计、溯源、用量统计
    :param request: 前端聊天请求对象
    :param contexts: 检索命中的文档块列表
    :param answer: 大模型返回的回答文本
    :param usage: token消耗统计字典（prompt/completion/total）
    """
    conn = None
    try:
        # 提取引用的chunk_id，转为json字符串存入数据库
        cited_ids = [str(c['chunk_id']) for c in contexts]
        # 简单敏感词检测：问题中包含敏感词则标记
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
    except Exception as e:
        print(f"Pro 版写入 MySQL 失败: {e}")
        traceback.print_exc()
    finally:
        if conn:
            conn.close()


def stream_and_save(request: ChatRequest, contexts: list):
    """
    流式生成答案的包装生成器：一边向前端输出SSE流，一边收集完整回答与token用量
    流式结束后：1. Redis累加当日租户token用量 2. 问答记录落库
    :param request: 聊天请求
    :param contexts: 检索得到的文档片段列表
    :yield: 逐块返回回答文本
    """
    full_answer = ""
    usage_data = {}

    for chunk in generate_stream(request.query, contexts):
        # 识别usage标记，不推送给前端，仅内部解析用量
        if chunk.startswith("__USAGE__"):
            usage_data = json.loads(chunk.replace("__USAGE__", ""))
            continue

        full_answer += chunk
        yield chunk

    # 流式输出完成后，更新租户当日token统计
    if usage_data:
        total_tokens = usage_data.get('total_tokens', 0)
        if total_tokens > 0:
            today_str = datetime.datetime.now().strftime('%Y%m%d')
            redis_key = f"tenant_token_usage:{request.tenant_id}:{today_str}"
            pipe = redis_client.pipeline()
            pipe.incrby(redis_key, total_tokens)
            pipe.expire(redis_key, 86400, nx=True)
            pipe.execute()

            # 保存本次问答记录到数据库
            save_chat_to_db(request, contexts, full_answer, usage_data)


@app.post("/api/chat")
async def chat_with_rag(request: ChatRequest):
    """
    RAG流式问答主接口（SSE流式输出）
    流程：租户额度校验 → BM25+向量RRF混合检索 → LLM流式回答 → 结束后统计用量+落库
    :param request: 聊天入参
    :return: StreamingResponse SSE流式响应
    """
    try:
        daily_limit = 500000
        today_str = datetime.datetime.now().strftime('%Y%m%d')
        redis_key = f"tenant_token_usage:{request.tenant_id}:{today_str}"
        used_tokens = int(redis_client.get(redis_key) or 0)

        # 判断租户当日token额度是否耗尽
        if used_tokens >= daily_limit:
            raise HTTPException(status_code=429, detail="专业版今日 Pro 额度已耗尽，请明天再试")

        # 执行混合检索（BM25 + 向量检索，RRF融合排序）
        contexts = hybrid_search(request.query, request.tenant_id, top_k=3)
        # 保护：检索为空也允许继续走LLM（无知识库兜底回答）
        if not isinstance(contexts, list):
            contexts = []

        # 返回SSE流式响应，内部自动收集答案、保存记录
        return StreamingResponse(
            stream_and_save(request, contexts),
            media_type="text/event-stream"
        )
    except HTTPException as he:
        raise he
    except Exception as e:
        print("==== /api/chat 异常堆栈 ====")
        traceback.print_exc()
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
        return {"code": 200, "message": f"租户 {tenant_id} 的缓存已刷新"}
    except Exception as e:
        print("==== /api/refresh_cache 异常堆栈 ====")
        traceback.print_exc()
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
        daily_limit = 500000
        today_str = datetime.datetime.now().strftime('%Y%m%d')
        redis_key = f"tenant_token_usage:{request.tenant_id}:{today_str}"
        used_tokens = int(redis_client.get(redis_key) or 0)

        if used_tokens >= daily_limit:
            raise HTTPException(status_code=429, detail="专业版今日 Pro 额度已耗尽，请明天再试")

        # 混合检索召回文档片段
        contexts = hybrid_search(request.query, request.tenant_id, top_k=3)
        if not isinstance(contexts, list):
            contexts = []

        # 同步调用大模型，一次性拿到答案和token消耗
        answer, usage_data = generate_sync(request.query, contexts)

        total_tokens = usage_data.get("total_tokens", 0)
        if total_tokens > 0:
            pipe = redis_client.pipeline()
            pipe.incrby(redis_key, total_tokens)
            pipe.expire(redis_key, 86400, nx=True)
            pipe.execute()

        # 问答记录入库
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
        print("==== /api/chat_test 异常堆栈 ====")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
