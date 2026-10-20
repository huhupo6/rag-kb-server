from rest_framework.decorators import api_view
from rest_framework.response import Response
from accounts.utils import check_tenant_active
from .services import retrieve_chunks, ask_llm, SENSITIVE_KEYWORDS
from .models import ChatHistory

# 员工提问 接口
@api_view(['POST'])
def ask_question(request):
    check_tenant_active(request.user)
    question = request.data.get('question', '').strip()

    if not question:
        return Response({'error': '请输入问题'}, status=400)

    tenant_id = request.user.tenant_id

    # 1. 检索相关文档块
    chunks = retrieve_chunks(question, tenant_id, top_k=3)
    
    # 2. 判断是否敏感
    is_sensitive = any(kw in question for kw in SENSITIVE_KEYWORDS)

    # 3. 调用大模型生成答案
    answer = ask_llm(question, chunks)

    # 4. 记录历史
    chat = ChatHistory.objects.create(
        tenant_id=tenant_id,
        user=request.user,
        question=question,
        answer=answer,
        cited_chunks=[c.id for c in chunks],
        is_sensitive=is_sensitive
    )

    return Response({
        'message': '回答成功',
        'chat_id': chat.id,
        'answer': answer,
        'is_sensitive': is_sensitive,
        'cited_count': len(chunks)
    }, status=200)

# 个人历史记录 接口
@api_view(['GET'])
def my_history(request):
    check_tenant_active(request.user)
    history = ChatHistory.objects.filter(user=request.user)
    
    data = [{
        'id': h.id,
        'question': h.question,
        'answer': h.answer,
        'created_at': h.created_at.strftime('%Y-%m-%d %H:%M:%S')
    } for h in history]

    return Response({'count': len(data), 'history': data}, status=200)

# HR/法务/董事问答日志查看 接口
@api_view(['GET'])
def view_logs(request):
    check_tenant_active(request.user)
    
    if request.user.role not in ['owner', 'admin']:
        return Response({'error': '无权限查看日志'}, status=403)

    logs = ChatHistory.objects.filter(tenant_id=request.user.tenant_id).select_related('user')
    
    data = []
    for log in logs:
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

    return Response({'count': len(data), 'logs': data}, status=200)