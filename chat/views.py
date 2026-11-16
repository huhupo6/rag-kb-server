from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.db.models import Sum
from rest_framework.pagination import PageNumberPagination
from django.utils import timezone

from common.redis_utils import redis_client
from common.permissions import check_tenant_active
from .services import retrieve_chunks, ask_llm, SENSITIVE_KEYWORDS
from .models import ChatHistory


@api_view(['POST'])
def ask_question(request):
    """
    Free版员工问答接口
    能力：MySQL全文检索RAG、租户每日token限流、敏感词标记、写入问答历史
    限制：仅free套餐可用，pro套餐走FastAPI混合检索微服务
    """
    check_tenant_active(request.user)

    # 套餐校验：pro版本禁止走这个free检索接口
    if request.user.tenant.plan_type == 'pro':
        return Response({
            'error': '非Free版本，请使用专业版检索接口'
        }, status=403)

    question = request.data.get('question', '').strip()
    if not question:
        return Response({'error': '请输入问题'}, status=400)

    tenant_id = request.user.tenant_id
    # Free版每日token上限 50000
    daily_limit = 50000
    today_str = timezone.now().strftime('%Y%m%d')
    redis_key = f"tenant_token_usage:{tenant_id}:{today_str}"

    # 读取当日已消耗token
    used_tokens = int(redis_client.get(redis_key) or 0)
    if used_tokens >= daily_limit:
        return Response({
            'error': '今日 AI 额度已耗尽，请明天再试或升级套餐'
        }, status=429)

    # 1. 召回文档片段（MySQL全文检索）
    chunks = retrieve_chunks(question, tenant_id, top_k=3)
    # 2. 敏感词检测标记
    is_sensitive = any(kw in question for kw in SENSITIVE_KEYWORDS)
    # 3. 调用LLM生成回答，拿到token消耗
    answer, usage = ask_llm(question, chunks)

    # 4. 入库聊天记录，用于审计、历史查询、用量对账
    chat = ChatHistory.objects.create(
        tenant_id=tenant_id,
        user=request.user,
        question=question,
        answer=answer,
        cited_chunks=[c.id for c in chunks],
        is_sensitive=is_sensitive,
        prompt_tokens=usage.get("prompt_tokens", 0),
        completion_tokens=usage.get("completion_tokens", 0),
        total_tokens=usage.get("total_tokens", 0)
    )

    # 5. Redis Pipeline 累加当日token用量，设置过期
    if usage.get("total_tokens", 0) > 0:
        pipe = redis_client.pipeline()
        pipe.incrby(redis_key, usage["total_tokens"])
        pipe.expire(redis_key, 90000)  # 25小时过期，容错跨时区
        pipe.execute()

    return Response({
        'message': '回答成功',
        'chat_id': chat.id,
        'answer': answer,
        'is_sensitive': is_sensitive,
        'cited_count': len(chunks),
        'tokens_used': usage.get("total_tokens", 0)
    }, status=200)


@api_view(['GET'])
def my_history(request):
    """
    用户个人问答历史接口
    分页展示自己的提问记录
    """
    check_tenant_active(request.user)

    history_qs = ChatHistory.objects.filter(user=request.user).order_by('-created_at')
    paginator = PageNumberPagination()
    paginator.page_size = 10

    paginated_history = paginator.paginate_queryset(history_qs, request)
    data = [{
        'id': h.id,
        'question': h.question,
        'answer': h.answer,
        'created_at': h.created_at.strftime('%Y-%m-%d %H:%M:%S')
    } for h in paginated_history]

    return paginator.get_paginated_response(data)


@api_view(['GET'])
def view_logs(request):
    """
    管理员日志审核接口
    owner（董事）：查看真实提问人姓名
    admin（HR/法务）：匿名展示员工，保护隐私
    """
    check_tenant_active(request.user)

    if request.user.role not in ['owner', 'admin']:
        return Response({'error': '无权限查看问答日志'}, status=403)

    logs_qs = ChatHistory.objects.filter(
        tenant_id=request.user.tenant_id
    ).select_related('user').order_by('-created_at')

    paginator = PageNumberPagination()
    paginator.page_size = 20
    paginated_logs = paginator.paginate_queryset(logs_qs, request)

    data = []
    for log in paginated_logs:
        if request.user.role == 'owner':
            asker_name = log.user.full_name if log.user else '已注销用户'
        else:
            asker_name = "匿名员工"

        data.append({
            'id': log.id,
            'asker': asker_name,
            'question': log.question,
            'answer': log.answer,
            'is_sensitive': log.is_sensitive,
            'created_at': log.created_at.strftime('%Y-%m-%d %H:%M:%S')
        })
    return paginator.get_paginated_response(data)


@api_view(['GET'])
def get_token_usage(request):
    """
    租户Token用量看板接口
    权限：仅owner、admin可见
    数据来源：Redis实时当日消耗 + MySQL历史累计消耗
    """
    check_tenant_active(request.user)

    if request.user.role not in ['owner', 'admin']:
        return Response({'error': '无权限查看用量看板'}, status=403)

    tenant = request.user.tenant
    tenant_id = tenant.id
    # 套餐额度区分：free 5万/天，pro 50万/天
    daily_limit = 50000 if tenant.plan_type == 'free' else 500000

    today_str = timezone.now().strftime('%Y%m%d')
    redis_key = f"tenant_token_usage:{tenant_id}:{today_str}"
    used_tokens_today = int(redis_client.get(redis_key) or 0)

    # MySQL统计全量历史累计token
    history_usage = ChatHistory.objects.filter(tenant_id=tenant_id).aggregate(
        total_prompt=Sum('prompt_tokens'),
        total_completion=Sum('completion_tokens'),
        total_all=Sum('total_tokens')
    )
    total_history_tokens = history_usage['total_all'] or 0

    return Response({
        'plan_type': tenant.plan_type,
        'daily_limit': daily_limit,
        'used_tokens_today': used_tokens_today,
        'remaining_tokens_today': max(0, daily_limit - used_tokens_today),
        'total_history_tokens': total_history_tokens,
        'updated_at': timezone.now().strftime('%Y-%m-%d %H:%M:%S')
    }, status=200)
