# Muse MCP 连接器

连接器通过 Huobao Drama 已公开的 `/api/v1` 路由读写剧本、分镜和素材。所有 HTTP 路由（包括 MCP）必须携带 `Authorization: Bearer <本机配置的令牌>`；令牌缺失或不匹配返回 401。只有 `/health` 不要求令牌，响应仅包含服务状态、令牌是否配置及白名单条数，不含令牌值。

## Compose 与公网

根目录 `docker-compose.yml` 一次构建并启动 Huobao（5679）和连接器（5680）。连接器默认绑定 `0.0.0.0:5680`，可用 `MUSE_CONNECTOR_BIND=127.0.0.1` 改回仅本机。公网开放 5680 后，任何人都能尝试令牌；建议用防火墙或隧道只放行可信来源。务必设置长随机令牌；令牌泄露立即更换并重建连接器。不要把令牌写进仓库、提示词或日志。

`ALLOWED_DRAMA_IDS` 为空时默认允许全部剧；配置逗号分隔 ID 时，仅这些剧可访问。`list_dramas` 列出当前允许的剧。新建剧 ID 会登记在连接器状态卷中，配置非空白名单时也能立即访问。删除剧后会移除登记。连接器状态卷保存新建剧登记和旧任务的一次重试标记。

## 工具

**剧集**：`list_dramas`、`get_drama`、`create_drama(title, description?)`、`delete_drama(drama_id)`。

**接手规则**：读取项目后调用 `get_project_guidelines(drama_id, agent_type="all", language="zh")` 获取当前项目启用的风格预设完整提示词，以及各平台 Agent 的有效 System Prompt 和技能。项目使用自定义风格文本时，`style.source` 为 `custom`，`style.prompt` 返回该文本；停用的预设不注入。可指定具体 `agent_type` 缩小返回内容。每次接手或配置更改后重新读取；明确的用户指令优先。内部 Agent 规则中提及的工具名须按 MCP 实际工具列表映射。规则对外部智能体以工具结果提供，模型是否遵守仍需检查生成结果。

**集**：`get_episode`、`create_episode(drama_id,title,order?)`、`update_episode(episode_id,fields_json)`、`update_episode_script`、`delete_episode`、`list_storyboards`。

**分镜**：`create_storyboard(episode_id,fields_json)`、`update_storyboard`、`delete_storyboard`、`upload_storyboard_media`、`delete_storyboard_media`。更新白名单涵盖标题、描述、镜头类型、角度、运镜、时长、视频/图片提示词、场景、地点、时间、氛围、结果、音乐提示词、音效及媒体字段。新建分镜只接受上游创建路由真实支持的字段。

**实体**：`create_character` / `update_character` / `delete_character`，以及 scene、prop 同类工具；`upload_drama_asset` 和 `delete_drama_asset` 管理已存在实体的图片记录。

**读取素材字节**：将 `character_sheet`、`scene_sheet`、`prop_sheet`、分镜图等字段中的 `static/...` 路径前加 `/`，向同一个 MCP 域名发送 `GET /static/...`，并携带与 `/mcp` 相同的 `Authorization: Bearer <令牌>`。响应是原始图片或视频字节，保留媒体类型；支持 `HEAD` 和 `Range`。缺失文件返回 404，错误令牌返回 401。路径不放入令牌，也不要把令牌加入 URL。此路径供 Muse 下载角色定妆照作为图像模型的真实参考输入。

**导入与校验**：`import_drama_bundle(bundle_json, drama_id?, episode_offset?)`、`verify_drama(drama_id)`。

**任务**：`list_tasks(drama_id?)`、`get_task(task_id)`、`retry_task(task_id)`。任务来源是 Huobao `/tasks` 接口，当前只公开图片/视频任务；不伪造文本任务或百分比，`progress_percent` 为 `null` 并说明后端未提供。后端没有 retry 路由，但任务记录保存原始生成参数；连接器按记录调用现有 `POST /tasks` 创建一个新任务，并在连接器状态卷记录每个旧任务最多一次。旧任务记录不变；请求失败也计为一次重试尝试。

## 导入格式与分批

`bundle_json` 接受用户约定的结构：`title`、`episodes[]`（`title`、`script_content`、`storyboards[]` 含 `scene_id`、`title`、`image_prompt`、`video_prompt`、`utterances`、`characters`、`scene`、`props`）以及 `assets[]`（`kind`、`name`、`filename`、`base64_data`）。可将 30 集拆为三次各 10 集调用：第一次不传 `drama_id`，保存返回 ID；后两次传同一 `drama_id`，并设置 `episode_offset` 为 10、20。按集号查重，按实体名字复用，单镜失败会保留此前成功并返回集号、镜号和原因。导入分镜次序按集内顺序生成。

上游现状限制：分镜 schema 没有 `utterances` 字段，连接器报告未写入；示例 `scene_id` 字符串是调用方标签，不是数据库场景 ID，场景关联按 `scene` 名称解析。上游没有把已存在角色/场景/道具关联到新集的公开路由，因此跨集复用会在报告中提示并可能导致分镜创建被后端拒绝；连接器不会编造关联接口。逐批导入后调用 `verify_drama` 检查集数、分镜数、已挂分镜图/视频、实体数与缺图名字。

## 删除语义与媒体限制

删除工具会先查询关联记录，成功返回被删 ID、类型、删除前关联数量；失败返回原后端 HTTP 状态、错误码和原因。Huobao 的实体与剧/集删除使用其既有删除语义。删除分镜图/视频或实体图会把数据库记录字段清成空字符串。上游没有公开物理文件删除 API，因此响应注明 `physical_file_deleted: false`；文件可能仍留在 Huobao 存储卷中。

图片大小上限 30 MiB。视频上限 100 MiB；仓库现有 `patches/storyboard-media-fields.patch` 在镜像构建时把上游上传路由限制同步提升到 100 MiB。超限错误包含实际字节数和上限。导入图片同样受 30 MiB 限制；大素材建议按需拆分资产导入调用。

## 健康检查和连接

```sh
docker compose up -d --build
curl -i http://127.0.0.1:5679/api/v1/health
curl -i http://127.0.0.1:5680/health
```

MCP 地址为 `http://主机:5680/mcp`。不要在仓库保存真实令牌。公网部署应以防火墙/可信来源隧道收敛访问面。
