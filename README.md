# Huobao Muse — 完整短剧制作系统

本仓库把 **Huobao Drama 完整应用** 与 Muse MCP 连接器整合为一套部署工程。Huobao Drama 上游代码作为固定版本的 Git 子模块保留；本仓库提供统一的构建、Docker Compose 部署和 Muse 接入配置。

## 仓库结构

- `huobao-drama/`：完整 Huobao Drama 上游源代码子模块，固定在 v4.0.10 对应提交。
- `muse-connector/`：Muse MCP 服务，负责安全读取项目、写入脚本/分镜和上传生成素材。
- `Dockerfile`：从子模块构建火宝前端与后端，并应用 Muse 分镜媒体字段补丁。
- `docker-compose.yml`：统一构建并启动 Huobao Drama 与 Muse 连接器。

## 获取完整源码

必须递归克隆，以同时检出 Huobao Drama 子模块：

```sh
git clone --recurse-submodules https://github.com/A-Yong1981/huobao-muse.git
cd huobao-muse
```

若已普通克隆：

```sh
git submodule update --init --recursive
```

## 部署

1. 复制 `.env.example` 为 `.env`，将 `MUSE_BRIDGE_TOKEN` 改为随机长令牌。
2. 一条 Compose 命令构建并启动火宝主应用与 Muse 连接器：

   ```sh
   docker compose up -d --build
   ```

3. 打开 `http://NAS地址:5679`，创建短剧并记下其 ID；把允许 Muse 访问的剧集 ID 写入 `.env` 的 `ALLOWED_DRAMA_IDS`，多个 ID 用逗号分隔。连接器默认拒绝所有未列出的剧集；更新白名单后重建连接器：

   ```sh
   docker compose up -d --force-recreate muse-connector
   ```

4. Muse MCP 地址为 `https://你的域名/huobao-muse/mcp`。经 HTTPS 反向代理或现有认证中转对外提供服务；连接器端口默认只绑定 `127.0.0.1`，不要直接暴露到公网。

配置和工作流详见 [Muse MCP connector 文档](docs/MUSE_CONNECTOR.md)。数据保存在 Docker 卷 `huobao-data`。

## 更新上游版本

本仓库固定使用已验证的上游子模块提交。更新火宝版本时，先更新 `huobao-drama` 子模块指针，再检查本仓库的 Dockerfile 补丁是否仍适用，最后重新构建镜像并提交新的子模块指针。

Huobao Drama 上游使用 CC BY-NC-SA 4.0 许可；详见 `huobao-drama/LICENSE`。本仓库未复制上游源码，子模块保留原始来源和许可信息。
