import logging
import requests
import time
from functools import wraps
from bm25_engine import get_bm25_engine
from milvus_engine import vector_search
from config import RERANK_URL, RERANK_TIMEOUT


logger = logging.getLogger(__name__)

# 重试装饰器：只重试1次，只针对超时/连接异常
def retry_rerank(max_retry: int = 1):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_err = None
            for attempt in range(max_retry + 1):
                try:
                    return func(*args, **kwargs)
                except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                    last_err = e
                    if attempt < max_retry:
                        logger.warning(f"Rerank调用失败，进行第{attempt+1}次重试，err: {e}")
                        time.sleep(0.3)
            raise last_err
        return wrapper
    return decorator

# 单独抽离rerank请求函数，方便加重试
@retry_rerank(max_retry=1)
def call_rerank_service(query: str, passages: list):
    resp = requests.post(
        RERANK_URL,
        json={
            "query": query,
            "passages": passages,
            "normalize": True
        },
        timeout=RERANK_TIMEOUT
    )
    resp.raise_for_status()
    return resp.json()["scores"]

# 结合 BM25 和 向量检索，使用 RRF 融合 + 独立Rerank微服务重排
def hybrid_search(query: str, tenant_id: int, top_k: int = 3) -> list:
    """
    混合检索链路：BM25召回 + Milvus向量召回 → RRF融合 → 调用Rerank微服务重排，服务异常自动降级为RRF结果
    :param query: 用户查询文本
    :param tenant_id: 租户ID（多租户隔离）
    :param top_k: 最终返回条数
    :return: [{"chunk_id": str, "content": str, "rerank_score": float}]
    """
    start_total = time.time()
    # 1. BM25 + 向量检索，各召回10条候选
    bm25_results = get_bm25_engine(tenant_id).search(query, top_k=10)
    vector_results = vector_search(query, tenant_id, top_k=10)
    logger.info(f"BM25召回数量：{len(bm25_results)}，向量召回数量：{len(vector_results)}")

    # 2. RRF分数融合
    rrf_scores = {}
    k_rrf = 60
    for rank, item in enumerate(bm25_results):
        chunk_id = str(item["chunk_id"])
        if chunk_id not in rrf_scores:
            rrf_scores[chunk_id] = {"score": 0.0, "content": item["content"]}
        rrf_scores[chunk_id]["score"] += 1.0 / (k_rrf + rank + 1)

    for rank, item in enumerate(vector_results):
        chunk_id = str(item["chunk_id"])
        if chunk_id not in rrf_scores:
            rrf_scores[chunk_id] = {"score": 0.0, "content": item["content"]}
        rrf_scores[chunk_id]["score"] += 1.0 / (k_rrf + rank + 1)
    
    # 按RRF分数降序，取前10个候选进入重排
    sorted_results = sorted(rrf_scores.items(), key=lambda x: x[1]["score"], reverse=True)
    top_10_candidates = sorted_results[:10]
    candidate_count = len(top_10_candidates)
    logger.info(f"RRF融合后候选文档数量：{candidate_count}")
    if candidate_count == 0:
        logger.info("没有召回任何候选文档")
        return []

    # 3. 调用独立Rerank微服务，失败自动降级RRF
    try:
        start_rerank = time.time()
        passages = [item[1]["content"] for item in top_10_candidates]
        rerank_scores = call_rerank_service(query, passages)
        rerank_cost = round(time.time() - start_rerank, 4)

        reranked_list = []
        for idx, (cid, data) in enumerate(top_10_candidates):
            reranked_list.append({
                "chunk_id": cid,
                "content": data["content"],
                "rerank_score": float(rerank_scores[idx])
            })
        # 按重排分数降序
        reranked_list.sort(key=lambda x: x["rerank_score"], reverse=True)
        final_result = reranked_list[:top_k]
        logger.info(f"Rerank微服务调用成功，重排耗时：{rerank_cost}s，最终返回{len(final_result)}条")

    except Exception as e:
        logger.error(f"Rerank服务异常，降级使用RRF融合结果：{e}")
        final_result = []
        # 降级：直接取RRF前top_k，把RRF分数放入rerank_score，上层逻辑不用改动
        for cid, data in top_10_candidates[:top_k]:
            final_result.append({
                "chunk_id": cid,
                "content": data["content"],
                "rerank_score": data["score"]
            })

    total_cost = round(time.time() - start_total, 4)
    logger.info(f"hybrid_search总耗时：{total_cost}s")
    return final_result