# Huobao Muse

Huobao Drama 的 Muse MCP 连接器。Muse 负责生成剧本、分镜提示词和媒体；Huobao Drama 继续保存项目、脚本、分镜及生成素材。连接器通过火宝现有 API 读写数据，不接触 AI 服务商密钥。

## 项目内容

- `muse-connector/`：带 Bearer token 鉴权和剧集白名单的 MCP 服务。
- `docker-compose.yml`：独立部署连接器。
- `docs/MUSE_CONNECTOR.md`：部署步骤、Muse 接入方式和工作流。
- `patches/storyboard-media-fields.patch`：火宝现有分镜 API 的素材字段补丁。

## 快速部署

1. 先按 `patches/storyboard-media-fields.patch` 给 Huobao Drama 后端应用小补丁并重新构建/启动；这样 Muse 上传的分镜图和首尾帧才能写回分镜记录。角色、场景、道具图以及分镜视频使用火宝已有接口。
2. 复制 `.env.example` 为 `.env`，设置随机的 `MUSE_BRIDGE_TOKEN`、火宝 API 地址和明确的 `ALLOWED_DRAMA_IDS`。
3. 启动：

   ```sh
   docker compose --profile muse up -d --build
   ```

4. 将 `https://你的域名/huobao-muse/mcp` 配置到 Muse，并使用 `Authorization: Bearer <MUSE_BRIDGE_TOKEN>`。异地 Muse 连接请经 HTTPS 反向代理或现有认证中转访问；不要把连接器端口直接暴露到公网。

完整配置见 [Muse MCP connector 使用说明](docs/MUSE_CONNECTOR.md)。

## 安全默认值

- `ALLOWED_DRAMA_IDS` 为空时不允许读取任何剧集。
- Compose 默认只绑定 `127.0.0.1:5680`。
- 连接器仅提供明确列出的读取、脚本/分镜更新和素材上传工具，不提供任意 API 代理、删除操作或模型服务商密钥。
- 不要提交真实 `.env`、令牌、项目数据库或生成媒体。

## 当前范围

这是 Muse 与火宝之间的内容/素材交接层，不会把 Muse 桌面端伪装成模型 API，也不会把 Muse 执行状态写入火宝 `sys_task` 任务面板。火宝原有 API 模型生成通道可继续使用。分阶段审核由 Muse 工作流提示词要求每阶段暂停，当前连接器不强制实施审批门禁。
