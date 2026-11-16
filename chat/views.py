from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.db.models import Sum
from rest_framework.pagination import PageNumberPagination
from django.utils import timezone

from accounts.utils import check_tenant_active, redis_client
from .services import retrieve_chunks, ask_llm, SENSITIVE_KEYWORDS
from .models import ChatHistory

# Free版 员工提问 接口
@api_view(['POST'])
def ask_question(request):
    check_tenant_active(request.user)

    # 仅供 Free 版使用
    if request.user.tenant.plan_type == 'pro':
        return Response({
            'error': '非Free版本'
        }, status=403)

    question = request.data.get('question', '').strip()

    if not question:
        return Response({'error': '请输入问题'}, status=400)

    tenant_id = request.user.tenant_id

    # Free 版每天最多 5万 Token
    daily_limit = 50000 
    redis_key = f"tenant_token_usage:{tenant_id}"

    # 获取当前自然日
    today_str = timezone.now().strftime('%Y%m%d')
    # 将日期拼接到 Redis Key 中
    redis_key = f"tenant_token_usage:{tenant_id}:{today_str}"

    # 获取当天已使用的 Token 数
    used_tokens = int(redis_client.get(redis_key) or 0)
    if used_tokens >= daily_limit:
        return Response({
            'error': '今日 AI 额度已耗尽，请明天再试或升级套餐'
        }, status=429)

    # 1. 检索相关文档块
    chunks = retrieve_chunks(question, tenant_id, top_k=3)
    
    # 2. 判断是否敏感
    is_sensitive = any(kw in question for kw in SENSITIVE_KEYWORDS)

    # 3. 调用大模型生成答案
    answer, usage = ask_llm(question, chunks)

    # 4. 记录历史
    chat = ChatHistory.objects.create(
        tenant_id=tenant_id,
        user=request.user,
        question=question,
        answer=answer,
        cited_chunks=[c.id for c in chunks],
        is_sensitive=is_sensitive
        prompt_tokens=usage.get("prompt_tokens", 0),
        completion_tokens=usage.get("completion_tokens", 0),
        total_tokens=usage.get("total_tokens", 0)
    )

    # 当日 token 用量统计 / 限流计数
    if usage.get("total_tokens", 0) > 0:
        pipe = redis_client.pipeline()
        pipe.incrby(redis_key, usage["total_tokens"])
        # 设置 25 小时过期
        pipe.expire(redis_key, 90000) 
        pipe.execute()

    return Response({
        'message': '回答成功',
        'chat_id': chat.id,
        'answer': answer,
        'is_sensitive': is_sensitive,
        'cited_count': len(chunks),
        'tokens_used': usage.get("total_tokens", 0)
    }, status=200)

# 个人历史记录 接口
@api_view(['GET'])
def my_history(request):
    check_tenant_active(request.user)

    # 1. 获取基础 QuerySet
    history_qs = ChatHistory.objects.filter(user=request.user).order_by('-created_at')

    # 2. 实例化分页器
    paginator = PageNumberPagination()
    paginator.page_size = 10

    # 3. 分页
    paginated_history = paginator.paginate_queryset(history_qs, request)
    
    data = [{
        'id': h.id,
        'question': h.question,
        'answer': h.answer,
        'created_at': h.created_at.strftime('%Y-%m-%d %H:%M:%S')
    } for h in paginated_history]

    return paginator.get_paginated_response(data)

# HR/法务/董事问答日志查看 接口
@api_view(['GET'])
def view_logs(request):
    check_tenant_active(request.user)
    
    if request.user.role not in ['owner', 'admin']:
        return Response({'error': '无权限查看日志'}, status=403)

    # 懒加载
    logs_qs = ChatHistory.objects.filter(
        tenant_id=request.user.tenant_id
    ).select_related('user').order_by('-created_at')

    # 分页处理
    paginator = PageNumberPagination()
    paginator.page_size = 20
    paginated_logs = paginator.paginate_queryset(logs_qs, request)

    data = []
    for log in paginated_logs:
        # 董事看真实姓名
        # HR/法务看匿名
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

# 获取当前租户的 Token 消耗 接口
@api_view(['GET'])
def get_token_usage(request):
    check_tenant_active(request.user)

    # 权限控制：只有董事和管理员可以看公司的总账单
    if request.user.role not in ['owner', 'admin']:
        return Response({'error': '无权限查看用量看板'}, status=403)

    tenant = request.user.tenant
    tenant_id = tenant.id

    # 1. 计算当前套餐的每日限额
    daily_limit = 50000 if tenant.plan_type == 'free' else 500000

    # 2. 从 Redis 获取今日已用额度
    today_str = timezone.now().strftime('%Y%m%d')
    redis_key = f"tenant_token_usage:{tenant_id}:{today_str}"
    used_tokens_today = int(redis_client.get(redis_key) or 0)

    # 3. 从 MySQL 统计历史总消耗 (只统计成功的问答)
    history_usage = ChatHistory.objects.filter(tenant_id=tenant_id).aggregate(
        total_prompt=Sum('prompt_tokens'),
        total_completion=Sum('completion_tokens'),
        total_all=Sum('total_tokens')
    )

    total_history_tokens = history_usage['total_all'] or 0

    # 4. 返回看板数据
    return Response({
        'plan_type': tenant.plan_type,
        'daily_limit': daily_limit,
        'used_tokens_today': used_tokens_today,
        'remaining_tokens_today': max(0, daily_limit - used_tokens_today),
        'total_history_tokens': total_history_tokens,
        'updated_at': timezone.now().strftime('%Y-%m-%d %H:%M:%S')
    }, status=200)
