# 使用官方 Python 3.11 轻量级镜像
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 设置环境变量，防止 Python 写 pyc 文件，并保证控制台输出不缓冲
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# 安装系统依赖 (某些库可能需要 gcc 等编译工具)
RUN apt-get update && apt-get install -y \
    default-libmysqlclient-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件并安装（使用清华源加速）
COPY requirements.txt /app/
RUN pip install --upgrade pip -i https://mirrors.aliyun.com/pypi/simple/ \
    && pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
# 复制项目所有代码到容器内
COPY . /app/

# 暴露 8000 端口
EXPOSE 8000

# 默认启动命令
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]