import os
from dotenv import load_dotenv
from pathlib import Path
from datetime import timedelta
from celery.schedules import crontab

# 加载.env文件中的环境变量
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# Django
SECRET_KEY = os.getenv('SECRET_KEY')
DEBUG = os.getenv('DEBUG', 'False') == 'True'

# 限制哪些域名 / 主机访问后端。
ALLOWED_HOSTS = ['*']

# 注册项目中启用的应用
INSTALLED_APPS = [
    'django.contrib.admin',         # 后台管理站点 
    'django.contrib.auth',          # 用户身份认证系统
    'django.contrib.contenttypes',  # 内容类型框架（通用外键）
    'django.contrib.sessions',      # Session会话
    'django.contrib.messages',      # 消息闪现（页面一次性提示）
    'django.contrib.staticfiles',   # 静态文件处理（css/js图片）
    'rest_framework',               # DRF
    'rest_framework_simplejwt',     # JWT
    'corsheaders',                  # 跨域头中间件

    # accounts app用于管理用户信息，租户隔离、用户身份认证、角色权限
    'accounts',
    # documents app用于管理知识库文档，文档上传、解析、分块、版本管理、分类
    'documents',
    # chat app用于管理问答交互，检索文档、调用大模型、保存问答记录、日志脱敏
    'chat',
]

# 自定义的用户模型
AUTH_USER_MODEL = 'accounts.User'

# 中间件
MIDDLEWARE = [
    "queryguard.middleware.QueryGuardMiddleware",
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

# 模板
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

# WSGI应用入口
WSGI_APPLICATION = 'kb_project.wsgi.application'

# 数据库配置
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

# 系统默认校验器
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
# 多语言翻译功能
USE_I18N = True
# 时区支持
USE_TZ = False
# 静态资源访问的 URL 前缀
STATIC_URL = 'static/'
# 默认主键字段指定
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Redis 配置
REDIS_HOST = os.getenv('REDIS_HOST', '127.0.0.1')
REDIS_PORT = os.getenv('REDIS_PORT', 6379)
REDIS_DB = os.getenv('REDIS_DB', 0)
REDIS_URL = f'redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}'

# Celery 配置
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = 'Asia/Shanghai'

# Celery Beat 定时任务配置
CELERY_BEAT_SCHEDULE = {
    'clean-expired-invitations': {
        'task': 'accounts.tasks.clean_expired_invitations',
        'schedule': crontab(hour=2, minute=0),   # 每天凌晨 2 点执行
    },
}

# JWT 配置
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'accounts.authentication.CustomJWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.AllowAny',
    ),
}

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(hours=24),   # Access Token 有效期 24 小时
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),    # Refresh Token 有效期 7 天
    'AUTH_HEADER_TYPES': ('Bearer',),
    'USER_ID_FIELD': 'id',
    'USER_ID_CLAIM': 'user_id',
}

# 邮件配置
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.qq.com'
EMAIL_PORT = 465
EMAIL_USE_SSL = True
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD')
DEFAULT_FROM_EMAIL = EMAIL_HOST_USER

# 媒体文件配置
MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')
# 上传文件大小限制50MB
DATA_UPLOAD_MAX_MEMORY_SIZE = 52428800
FILE_UPLOAD_MAX_MEMORY_SIZE = 52428800

# 前端跨域访问后端
CORS_ALLOW_ALL_ORIGINS = True
# CORS_ALLOWED_ORIGINS = []

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {
        "queryguard": {
            "handlers": ["console"],
            "level": "DEBUG",
            "propagate": False,
        },
    },
}