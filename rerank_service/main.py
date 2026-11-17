from fastapi import FastAPI
from pydantic import BaseModel
from FlagEmbedding import FlagReranker
import logging

logger = logging.getLogger(__name__)
app = FastAPI(title="Rerank Service")

# 全局单例加载重排模型
_reranker = None

class RerankRequest(BaseModel):
    query: str
    passages: list[str]
    normalize: bool = True

class RerankResponse(BaseModel):
    scores: list[float]

def get_reranker():
    global _reranker
    if _reranker is None:
        logger.info("Rerank服务正在加载 BAAI/bge-reranker-base-v1.5 ...")
        # 直接指向容器内本地模型目录，禁止联网拉取
        _reranker = FlagReranker(
            "/app/models/bge-reranker-base",
            use_fp16=False
        )
        logger.info("Reranker加载完成")
    return _reranker

@app.post("/rerank", response_model=RerankResponse)
def rerank(req: RerankRequest):
    reranker = get_reranker()
    pairs = [[req.query, text] for text in req.passages]
    scores = reranker.compute_score(pairs, normalize=req.normalize)
    return RerankResponse(scores=[float(s) for s in scores])

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000)
