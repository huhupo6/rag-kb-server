from django.urls import path
from . import views

app_name = 'chat'

urlpatterns = [
    # 员工提问
    path('ask/', views.ask_question, name='ask'),
    # 个人历史记录
    path('history/', views.my_history, name='my_history'),
    # HR/法务/董事问答日志查看
    path('logs/', views.view_logs, name='view_logs'),
]