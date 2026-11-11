# Django Web 后端容器构建文件
# 说明：用于构建项目 Django + DRF 业务后端镜像
# 内置 MySQL 客户端编译依赖、Python 环境、项目代码
# 镜像构建后，由主 docker-compose.yml 编排启动
# 服务端口：容器内 8000

# 使用官方 Python 3.11 镜像
FROM python:3.11-slim-bookworm

# 设置容器内的工作目录为 /app
WORKDIR /app

# 设置环境变量，防止 Python 写 pyc 文件
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# 阿里云镜像
RUN rm -f /etc/apt/sources.list.d/debian.sources && \
    echo "deb http://mirrors.aliyun.com/debian/ bookworm main non-free non-free-firmware contrib" > /etc/apt/sources.list && \
    echo "deb http://mirrors.aliyun.com/debian-security/ bookworm-security main" >> /etc/apt/sources.list && \
    echo "deb http://mirrors.aliyun.com/debian/ bookworm-updates main non-free non-free-firmware contrib" >> /etc/apt/sources.list

# 安装基础系统依赖
# default-libmysqlclient-dev：mysqlclient 编译需要的库
# gcc / g++ / make：C/C++ 编译器，用于编译一些带 C 扩展的 Python 包
# 安装完成后删除 apt 缓存，减小镜像体积
RUN apt-get update && apt-get install -y --no-install-recommends \
    default-libmysqlclient-dev \
    gcc \
    g++ \
    make \
    && rm -rf /var/lib/apt/lists/*

# 复制 Python 依赖清单到容器内
COPY requirements.txt /app/

# 使用阿里云 PyPI 镜像升级 pip，然后安装项目全部 Python 依赖
RUN pip install --upgrade pip -i https://mirrors.aliyun.com/pypi/simple/ \
    && pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

# 复制当前目录所有项目代码到容器内 /app
COPY . /app/

# 声明容器内部对外提供服务的端口
EXPOSE 8000

# 启动 Django 内置开发服务器
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]