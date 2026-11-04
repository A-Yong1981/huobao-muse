# Huobao Muse — 完整短剧制作系统

本仓库将完整 Huobao Drama 应用与 Muse MCP 连接器整合为一套 Compose 项目。`huobao-drama/` 为上游子模块，本部署还包含设置页和存储统计的定向改动。

本仓库在 `patches/huobao-backend-custom.patch` 和 `patches/huobao-frontend-custom.patch` 中保存子模块的本地改造；Docker 构建时自动应用。若要在本机直接运行源码，请在初始化子模块后，分别在 `huobao-drama` 目录执行 `git apply ../patches/huobao-backend-custom.patch` 与 `git apply ../patches/huobao-frontend-custom.patch`。

## 获取与启动

```sh
git clone --recurse-submodules https://github.com/A-Yong1981/huobao-muse.git
cd huobao-muse
cp .env.example .env
```

在 `.env` 中设置长随机 `MUSE_BRIDGE_TOKEN`，然后运行：

```sh
docker compose up -d --build
```

网页入口是 NAS 的 `1241` 端口，先经过 OTP 网关，再进入容器内的 Huobao `5679`。当前 OECT 隧道将 `hb.aynas.de5.net` 指向 NAS `1241`，将 `hb-mcp.aynas.de5.net` 指向 NAS `5680`；因此该 NAS 的 `.env` 要把 `MUSE_CONNECTOR_BIND` 设为 `192.168.58.200`。`18081` 仅供 NAS 本机检查。网页设置页可以用当前 Muse 令牌验证后随机生成或自定义新令牌；令牌保存在连接器数据卷，立即生效，重启后保留。

首次绑定 OTP 前，需要在 `.env` 设置 `WEB_AUTH_BOOTSTRAP_HASH`。用下列命令交互输入首次绑定密码，命令不会在终端显示明文密码：

```sh
python3 - <<'PY'
import getpass, hashlib, secrets
password = getpass.getpass('First OTP binding password: ')
salt = secrets.token_bytes(16)
print(salt.hex() + '$' + hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 200000).hex())
PY
```

将输出填入 `.env` 的 `WEB_AUTH_BOOTSTRAP_HASH` 后启动服务。首次访问 `/auth` 输入绑定密码并扫码；之后用动态码登录，可选择信任设备 30 天。已登录设备换绑时需再次输入原验证器动态码。OTP 状态保存在 `web-auth-data` 卷中；丢失验证器后需要在 NAS 上恢复备份或由管理员重置该卷的数据。

网页设置的「通用」页可换绑 OTP；「AI 服务」页底部管理 Muse MCP 令牌；「存储位置」页可手动下载全部剧本与本机素材的 `tar.gz` 归档。导出接口为 `GET /api/v1/storage/export`，归档含 `project-data.json`、逐集剧本文本及 `static/` 和已有的 ArcReel 旧素材目录，不含模型 API Key、连接令牌或完整数据库。

`ALLOWED_DRAMA_IDS` 为空表示全部剧允许；设置逗号分隔 ID 后按配置过滤。新建剧会在连接器侧登记，创建后立即可读写，无需重建。`/health` 只报告状态、令牌配置状态及白名单数量，不返回令牌；其余所有 MCP 路由始终校验 Bearer。

功能、导入分批格式、删除行为和上游接口限制见 [Muse 连接器文档](docs/MUSE_CONNECTOR.md)。镜像使用仓库现有 patch 适配分镜媒体字段并将视频上传上限设为 100 MiB；图片上限为 30 MiB。
