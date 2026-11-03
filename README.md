# Huobao Muse — 完整短剧制作系统

本仓库将完整 Huobao Drama 应用与 Muse MCP 连接器整合为一套 Compose 项目。`huobao-drama/` 为固定上游子模块；本仓库提供统一构建和部署，不改写上游源码。

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

Huobao Web 使用 5679，Muse MCP 使用 5680。连接器端口默认绑定 `0.0.0.0`；公网开放后任何人都能尝试令牌，建议配合防火墙或隧道只放行可信来源。令牌泄露后立即更换并重建连接器。将 `MUSE_CONNECTOR_BIND` 设为 `127.0.0.1` 可限制为本机访问。

`ALLOWED_DRAMA_IDS` 为空表示全部剧允许；设置逗号分隔 ID 后按配置过滤。新建剧会在连接器侧登记，创建后立即可读写，无需重建。`/health` 只报告状态、令牌配置状态及白名单数量，不返回令牌；其余所有 MCP 路由始终校验 Bearer。

功能、导入分批格式、删除行为和上游接口限制见 [Muse 连接器文档](docs/MUSE_CONNECTOR.md)。镜像使用仓库现有 patch 适配分镜媒体字段并将视频上传上限设为 100 MiB；图片上限为 30 MiB。
