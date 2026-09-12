# HfO2-FerroKG 网页访问与公网部署说明

## 为什么不连同一个内网就打不开

当前网页是 Streamlit 本地服务：

- `http://127.0.0.1:8501`：只代表这台电脑自己，手机和别人电脑不能用这个地址。
- `http://10.x.x.x:8501` 或 `http://192.168.x.x:8501`：只在同一个 Wi-Fi/局域网里可用。
- 如果手机不连接同一个 Wi-Fi，或者别人不在你的局域网，就必须使用公网部署或内网穿透。

## 三种访问方式

### 1. 本机使用

适合你自己在电脑上调试。

```bash
# 换成你本地的实际检出路径
cd "/path/to/hfo2-ferro-kg"
.venv/bin/python -m streamlit run app/Home.py --server.port 8501 --server.headless true
```

打开：

```text
http://127.0.0.1:8501
```

### 2. 同一 Wi-Fi 手机访问

适合你手机和电脑在同一个局域网。

先查电脑局域网 IP：

```bash
ipconfig getifaddr en0
```

如果输出是 `10.1.23.170`，手机打开：

```text
http://10.1.23.170:8501
```

如果打不开，通常是 Mac 防火墙或 Wi-Fi 隔离导致。不要把路由器端口转发到公网。

### 3. 外网访问或给别人使用

推荐顺序：

1. 腾讯云正式部署：适合长期给自己或合作者使用。
2. Cloudflare Tunnel / ngrok 临时隧道：适合短时间演示。
3. Streamlit Community Cloud：只适合不含私有 PDF、数据库、API key 的公开 demo。

## 推荐方案：腾讯云部署

适合这个项目，因为系统里包含 PDF、数据库、模型输出和可能的 API key。

基本做法：

1. 在腾讯云服务器安装 Python、Git、系统依赖。
2. 上传或拉取项目代码，但不要上传 `.env`、API key、原始 PDF 版权文件到公开仓库。
3. 在服务器私有目录创建 `.env`。
4. 用 `tmux`、`systemd` 或 Docker 后台运行 Streamlit。
5. 用 Nginx 反向代理到 Streamlit，并加 HTTPS 和登录保护。
6. 安全组只开放 `443`，不要直接暴露 `8501` 到公网。

最小运行命令示例：

```bash
cd /root/home/hfo2_ferrokg/hfo2-ferro-kg
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m streamlit run app/Home.py --server.address 127.0.0.1 --server.port 8501 --server.headless true
```

注意这里绑定 `127.0.0.1`，再交给 Nginx 对外服务。

## 临时方案：隧道

临时演示可以用 Cloudflare Tunnel 或 ngrok，把本机 `8501` 映射到一个临时公网地址。

使用前必须确认：

- 不在网页中显示真实 API key。
- 不开放 PDF 原文下载给无关人员。
- 不把数据库、模型文件、日志目录作为静态文件暴露。
- 用完立即关闭隧道。

## 安全边界

这个项目不要直接裸奔到公网，原因是：

- 文献 PDF 可能有版权限制。
- `.env` 里可能有 DashScope、PaddleOCR、Materials Project 等 key。
- 数据库里有完整解析文本、图谱和模型结果。
- Streamlit 默认没有严肃的用户权限系统。

因此：

- 本地调试：`127.0.0.1:8501`
- 手机同 Wi-Fi：局域网 IP
- 外网长期使用：腾讯云 + Nginx + HTTPS + 登录
- 外网临时演示：隧道 + 访问控制 + 用完关闭

