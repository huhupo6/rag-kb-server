from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from rag_engine import hybrid_search
from llm_service import generate_stream, generate_sync
from bm25_engine import get_bm25_engine, clear_bm25_engine

app = FastAPI(title="RAG 检索增强 API", version="1.0")

class ChatRequest(BaseModel):
    tenant_id: int
    query: str

# 测试接口
@app.get("/")
def read_root():
    return {"message": "FastAPI RAG 引擎已就绪"}

# 混合检索 + 流式输出
@app.post("/api/chat")
async def chat_with_rag(request: ChatRequest):
    try:
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