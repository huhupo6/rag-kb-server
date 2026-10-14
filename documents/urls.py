from django.urls import path
from . import views

app_name = 'documents'

urlpatterns = [
    # 文档上传
    path('upload/', views.upload_document, name='upload'),
    # 文档查询
    path('', views.list_documents, name='list'),
    # 新版本的上传
    path('<int:doc_id>/new-version/', views.upload_new_version, name='new_version'),
    # 文档历史版本查询
    path('<int:doc_id>/versions/', views.document_versions, name='versions'),
    # 文档软删
    path('<int:doc_id>/', views.delete_document, name='delete'),
]