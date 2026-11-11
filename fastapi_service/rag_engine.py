import logging
from bm25_engine import get_bm25_engine
from milvus_engine import vector_search

logger = logging.getLogger(__name__)

# 结合 BM25 和 向量检索，使用 RRF 融合
def hybrid_search(query: str, tenant_id: int, top_k: int = 3) -> list:
    
    # 1. 分别获取两路结果 (各取 Top 10，方便后续融合)
    bm25_results = get_bm25_engine(tenant_id).search(query, top_k=10)
    vector_results = vector_search(query, tenant_id, top_k=10)

    # 2. 准备 RRF 融合
    rrf_scores = {}
    k = 60

    # 计算 BM25 的 RRF 分数
    for rank, item in enumerate(bm25_results):
        chunk_id = str(item["chunk_id"])
        if chunk_id not in rrf_scores:
            rrf_scores[chunk_id] = {"score": 0.0, "content": item["content"]}
        rrf_scores[chunk_id]["score"] += 1.0 / (k + rank + 1)
    
    # 计算 向量检索 的 RRF 分数
    for rank, item in enumerate(vector_results):
        chunk_id = str(item["chunk_id"])
        if chunk_id not in rrf_scores:
            rrf_scores[chunk_id] = {"score": 0.0, "content": item["content"]}
        rrf_scores[chunk_id]["score"] += 1.0 / (k + rank + 1)
    
    # 3. 按总分降序排序
    sorted_results = sorted(rrf_scores.items(), key=lambda x: x[1]["score"], reverse=True)

    # 4. 提取最终的 Top K
    final_results = []
    for chunk_id, data in sorted_results[:top_k]:
        final_results.append({
            "chunk_id": chunk_id,
            "content": data["content"],
            "rrf_score": data["score"]
        })

    logger.info(f"融合完毕，为用户找到了 {len(final_results)} 个最相关的上下文块")
    return final_results