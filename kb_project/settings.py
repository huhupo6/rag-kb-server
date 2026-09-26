import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# Django
SECRET_KEY = os.getenv('SECRET_KEY')
DEBUG = os.getenv('DEBUG', 'False') == 'True'

# 校验请求头 Host
ALLOWED_HOSTS = []

INSTALLED_APPS = [
    'django.contrib.admin',         # 后台管理站点 
    'django.contrib.auth',          # 用户身份认证系统
    'django.contrib.contenttypes',  # 内容类型框架（通用外键）
    'django.contrib.sessions',      # Session会话
    'django.contrib.messages',      # 消息闪现（页面一次性提示）
    'django.contrib.staticfiles',   # 静态文件处理（css/js图片）
    'rest_framework',               # DRF
    'corsheaders',                  # 跨域头中间件

    # accounts app用于管理用户信息，租户隔离、用户身份认证、角色权限
    'accounts',
    # documents app用于管理知识库文档，文档上传、解析、分块、版本管理、分类
    'documents',
    # chat app用于管理问答交互，检索文档、调用大模型、保存问答记录、日志脱敏
    'chat',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',                    # 前后端跨域请求
    'django.middleware.security.SecurityMiddleware',            # 添加安全响应头
    'django.contrib.sessions.middleware.SessionMiddleware',     # 管理session会话
    'django.middleware.common.CommonMiddleware',                # URL优化处理
    'django.middleware.csrf.CsrfViewMiddleware',                # CSRF跨站请求伪造防护
    'django.contrib.auth.middleware.AuthenticationMiddleware',  # 加载登录用户
    'django.contrib.messages.middleware.MessageMiddleware',     # 一次性flash提示消息
    'django.middleware.clickjacking.XFrameOptionsMiddleware',   # 禁止iframe嵌套
]

# 根路由
ROOT_URLCONF = 'kb_project.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'kb_project.wsgi.application'

# MySQL
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': os.getenv('DB_NAME'),
        'USER': os.getenv('DB_USER'),
        'PASSWORD': os.getenv('DB_PASSWORD'),
        'HOST': os.getenv('DB_HOST'),
        'PORT': os.getenv('DB_PORT'),
        'OPTIONS': {'charset': 'utf8mb4',},
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# 简体中文
LANGUAGE_CODE = 'zh-hans'

# 北京时间
TIME_ZONE = 'Asia/Shanghai'

USE_I18N = True

USE_TZ = False

STATIC_URL = 'static/'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# 前端跨域访问后端
CORS_ALLOW_ALL_ORIGINS = True

# CORS_ALLOWED_ORIGINS = []