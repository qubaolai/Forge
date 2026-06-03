# 后端 API 实现规范

> 本文档面向后端开发者,前端基于此契约实现,接入时需严格遵循。

## 一、通用约定

### 1.1 基础信息

| 项 | 值 |
|---|---|
| Base URL | `/api/v1` |
| 认证方式 | JWT(access token + refresh token) |
| 编码 | UTF-8 |
| Content-Type | `application/json`(除文件上传) |
| 时间格式 | ISO 8601 字符串(UTC),如 `2025-05-10T08:30:00.000Z` |
| ID 格式 | 字符串,推荐 `<prefix>_<nanoid>`,如 `kb_abc123` |

### 1.2 统一响应格式

**所有 JSON 响应**(非 SSE 流式)必须统一为以下结构:

```json
{
  "code": 0,
  "data": <业务数据>,
  "message": "ok",
  "details": { ... }
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `code` | int | 业务状态码,`0` 表示成功,非 0 表示失败 |
| `data` | any | 业务数据,失败时为 `null` |
| `message` | string | 用户可读的描述,失败时是错误信息 |
| `details` | object? | 失败时附加的结构化错误细节,可选 |

**HTTP 状态码 与 code 的关系**:
- HTTP 200 + `code=0`:成功
- HTTP 4xx/5xx:失败,**body 仍需是统一格式**(便于前端拦截器统一处理)

### 1.3 业务错误码规范

错误码采用 5 位数字,前 3 位对应 HTTP 状态码,后 2 位是细分序号:

| 范围 | 含义 | 示例 |
|---|---|---|
| `40000-40099` | 通用 400(参数错误) | `40001` 缺少必填字段 |
| `40100-40199` | 401(认证失败) | `40101` token 过期 |
| `40300-40399` | 403(权限不足) | `40301` 角色权限不足 |
| `40400-40499` | 404(资源不存在) | `40401` 用户不存在 |
| `40900-40999` | 409(冲突) | `40901` 名称已被占用 |
| `42900-42999` | 429(限流) | `42901` 调用频率超限 |
| `50000-50999` | 500(服务端错误) | `50001` 数据库异常 |

具体错误码列表由后端维护,前端只需识别 `code` 是否为 `0`。

### 1.4 分页约定

**请求参数**(query string):
```
?page=1&page_size=20&q=keyword
```

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `page` | int | 1 | 页码,从 1 开始 |
| `page_size` | int | 20 | 每页数量,上限 100 |
| `q` | string | "" | 搜索关键词,可选 |

**响应**(置于 `data` 字段):
```json
{
  "items": [...],
  "total": 156,
  "page": 1,
  "page_size": 20
}
```

### 1.5 认证

**access token**:
- 通过 `Authorization: Bearer <token>` header 传递
- 有效期 1 小时
- 过期后调 `/auth/refresh` 续期

**refresh token**:
- 通过 **httpOnly cookie** 传递,前端无法读取
- 有效期 30 天
- cookie 配置:`HttpOnly; Secure; SameSite=Strict; Path=/api/v1/auth`

**登录流程**:
```
1. POST /auth/login {email, password}
   → 响应 body: {access_token, expires_at, user}
   → 响应 Set-Cookie: refresh_token=xxx; HttpOnly; ...
2. 后续请求带 Authorization header
3. access 过期 → 401 → 前端调 /auth/refresh(自动携带 cookie)
   → 响应新的 access_token
4. 登出:POST /auth/logout
   → 响应 Set-Cookie: refresh_token=; Max-Age=0(清除 cookie)
   → 服务端将 refresh_token 加入黑名单
```

---

## 二、接口清单

下表是当前前端依赖的全部接口。**勾选**标记表示前端已实现且必须支持。

### 2.1 认证 `/auth`

| 方法 | 路径 | 用途 | 必需 |
|---|---|---|---|
| POST | `/auth/login` | 邮箱密码登录 | ✅ |
| POST | `/auth/logout` | 登出,清除 refresh cookie | ✅ |
| POST | `/auth/refresh` | 刷新 access token | ✅ |
| GET | `/auth/me` | 当前用户信息 | ✅ |
| POST | `/auth/change-password` | 修改密码 | 后续 |

### 2.2 会话 `/sessions`

| 方法 | 路径 | 用途 | 必需 |
|---|---|---|---|
| GET | `/sessions` | 当前用户会话列表 | ✅ |
| POST | `/sessions` | 新建会话(指定 agent_id) | ✅ |
| GET | `/sessions/{id}` | 会话详情 | ✅ |
| PATCH | `/sessions/{id}` | 重命名 | ✅ |
| DELETE | `/sessions/{id}` | 删除会话 | ✅ |
| GET | `/sessions/{id}/messages` | 历史消息(分页) | ✅ |

### 2.3 对话 `/chat`

| 方法 | 路径 | 用途 | 必需 |
|---|---|---|---|
| POST | `/chat/completions` | **SSE 流式**,核心对话接口 | ✅ |
| POST | `/chat/stop` | 中断指定消息生成 | ✅ |
| POST | `/chat/regenerate` | 重新生成 | 后续 |

### 2.3.1 任务运行 `/runs` + 决策 `/decisions`

| 方法 | 路径 | 用途 | 必需 |
|---|---|---|---|
| POST | `/runs` | 创建并启动任务 run（`mode=plan_exec/workflow`） | ✅ |
| GET | `/runs` | 列出 run（按 workspace） | ✅ |
| GET | `/runs/{id}` | 查询 run 快照 | ✅ |
| GET | `/runs/{id}/events` | SSE 订阅 run 事件 | ✅ |
| POST | `/runs/{id}/abort` | 中止 run | ✅ |
| GET | `/decisions/{token}` | 查询待决策项（Plan/Gate） | ✅ |
| POST | `/decisions/{token}` | 提交决策（批准/拒绝） | ✅ |

说明：

1. `/runs` 的人工决策入口是 `/decisions/{token}`，不再使用 `/runs/{id}/decide`。
2. `token` 由 run 事件流返回（例如 `plan_decision_required`、`workflow_gate_required`）。

### 2.4 知识库 `/knowledge-bases`

| 方法 | 路径 | 用途 | 必需 |
|---|---|---|---|
| GET | `/knowledge-bases` | 列表 | ✅ |
| POST | `/knowledge-bases` | 创建 | ✅ |
| GET | `/knowledge-bases/{id}` | 详情 | ✅ |
| PATCH | `/knowledge-bases/{id}` | 更新 | ✅ |
| DELETE | `/knowledge-bases/{id}` | 删除 | ✅ |
| POST | `/knowledge-bases/{id}/retrieve` | 检索测试 | ✅ |

### 2.5 文档 `/knowledge-bases/{kbId}/documents`

| 方法 | 路径 | 用途 | 必需 |
|---|---|---|---|
| GET | `.../documents` | 文档列表 | ✅ |
| POST | `.../documents/upload` | 上传文件 | ✅ |
| POST | `.../documents/url` | 添加 URL | 后续 |
| GET | `.../documents/{docId}` | 文档详情 | ✅ |
| GET | `.../documents/{docId}/chunks` | 分块列表 | 后续 |
| POST | `.../documents/{docId}/reindex` | 重新索引 | 后续 |
| DELETE | `.../documents/{docId}` | 删除 | ✅ |

### 2.6 模型 `/models`

| 方法 | 路径 | 用途 | 必需 |
|---|---|---|---|
| GET | `/models?model_type=chat` | 可用 Chat 模型列表，按供应商分组 | ✅(前端会查) |
| GET | `/models/{id}` | 管理端读取模型详情 | ✅ |
| PUT | `/models/{id}` | 管理端更新模型与类型配置 | ✅ |
| DELETE | `/models/{id}` | 管理端删除模型 | ✅ |
| GET | `/admin/model-bindings` | 读取系统模型绑定 | ✅ |
| PUT | `/admin/model-bindings/{role}` | 切换系统模型绑定 | ✅ |
| GET | `/admin/rag-index/status` | 读取 RAG 向量索引状态 | ✅ |
| POST | `/admin/rag-index/rebuild` | 提交 RAG 向量索引重建 | ✅ |

`GET /models` 返回统一模型结构，不再返回旧的 `ModelEndpoint[]`：

```json
{
  "groups": [
    {
      "provider": "openai",
      "models": [
        {
          "provider": "openai",
          "model_id": "123",
          "name": "gpt-4o",
          "display_name": "GPT-4o",
          "model_type": "chat",
          "config": {
            "context_window": 128000,
            "max_output_tokens": 4096,
            "capabilities": ["tools"]
          },
          "thinking": null
        }
      ]
    }
  ],
  "models": []
}
```

### 2.7 暂不实现(单 Agent 模式)

以下接口前端代码中存在,但**当前阶段后端可不实现**(前端会隐藏入口):

- `/agents/*` (列表/创建/更新/删除/复制)
- `/tools` (工具列表)
- `/users/*` (用户管理)
- `/audit-logs` (审计日志)

**单 Agent 模式约定**:`POST /sessions` 时不传 `agent_id` 或传一个固定值(如 `"default"`),后端用配置文件里的默认 Agent 配置。

---

## 三、关键接口详解

### 3.1 登录 `POST /auth/login`

**请求**:
```json
{
  "email": "user@example.com",
  "password": "secret123"
}
```

**成功响应**:
```json
{
  "code": 0,
  "data": {
    "access_token": "eyJhbGc...",
    "expires_at": "2025-05-10T09:30:00.000Z",
    "user": {
      "id": "user_xxx",
      "email": "user@example.com",
      "name": "张三",
      "avatar_url": null,
      "role": "member",
      "status": "active",
      "created_at": "...",
      "updated_at": "..."
    }
  },
  "message": "ok"
}
```

同时设置 cookie:`Set-Cookie: refresh_token=xxx; HttpOnly; Secure; SameSite=Strict; Max-Age=2592000; Path=/api/v1/auth`

**失败响应示例**:
```json
{ "code": 40402, "data": null, "message": "密码错误" }
```
HTTP 状态码 401。

### 3.2 SSE 对话 `POST /chat/completions`

**请求**:
```json
{
  "session_id": "sess_xxx",
  "message": "v2.0 有哪些新功能?",
  "attachments": []
}
```

**响应** `Content-Type: text/event-stream`,响应体是按 SSE 协议分隔的事件流:

```
data: {"type":"message_start","message_id":"msg_abc","session_id":"sess_xxx"}

data: {"type":"tool_call","tool_call":{"id":"tc_1","tool_id":"retrieval","tool_name":"知识库检索","arguments":{"query":"v2.0 新功能"},"status":"running"}}

data: {"type":"tool_result","tool_call_id":"tc_1","status":"success","result":{"matched":3}}

data: {"type":"citations","citations":[{"index":1,"chunk_id":"chunk_1","document_id":"doc_1","document_name":"产品手册.pdf","content":"v2.0 引入...","score":0.92,"metadata":{"page":12}}]}

data: {"type":"delta","content":"v2.0"}

data: {"type":"delta","content":" 主要"}

data: {"type":"delta","content":"带来了..."}

data: {"type":"done","finish_reason":"stop","usage":{"prompt_tokens":120,"completion_tokens":256,"total_tokens":376}}
```

**SSE 实现要点**(后端必须遵守):

1. **每个事件以双换行 `\n\n` 分隔**(SSE 协议标准)
2. **每行以 `data: ` 开头**
3. **响应头**:
   ```
   Content-Type: text/event-stream
   Cache-Control: no-cache
   Connection: keep-alive
   X-Accel-Buffering: no   # 禁用 Nginx 等代理的响应缓冲(关键!)
   ```
4. **不要被中间件压缩**(关掉 gzip,否则会被攒包)
5. **Python 推荐**:FastAPI 的 `StreamingResponse` 或 `EventSourceResponse`(sse-starlette)

**事件类型完整定义**:

| type | 字段 | 说明 |
|---|---|---|
| `message_start` | `message_id`, `session_id` | 流开始,告知 assistant 消息 ID |
| `delta` | `content: string` | 文本增量,前端累加显示 |
| `tool_call` | `tool_call: ToolCall` | 开始调用工具 |
| `tool_result` | `tool_call_id`, `status`, `result` | 工具调用结果 |
| `citations` | `citations: Citation[]` | RAG 检索结果(数组追加) |
| `done` | `finish_reason`, `usage` | 流正常结束 |
| `error` | `message`, `code?` | 错误,流终止 |

事件顺序通常为:`message_start` → `tool_call` → `tool_result` → `citations` → `delta...delta` → `done`。

**中断**:客户端断开连接(关闭 fetch),后端应能感知并清理资源。也可主动调 `POST /chat/stop {message_id}` 中断。

### 3.3 文档上传 `POST /knowledge-bases/{kbId}/documents/upload`

**请求**:`Content-Type: multipart/form-data`,字段 `file`(单文件)

**响应**:立即返回(异步入队解析)
```json
{
  "code": 0,
  "data": {
    "id": "doc_xxx",
    "kb_id": "kb_xxx",
    "name": "产品手册.pdf",
    "source": "upload",
    "mime_type": "application/pdf",
    "size_bytes": 102400,
    "status": "pending",
    "progress": 0,
    "chunk_count": 0,
    "created_at": "...",
    "updated_at": "..."
  },
  "message": "ok"
}
```

**状态机**:
```
pending → parsing → chunking → embedding → indexed
                 ↘ failed (任一阶段失败)
```

前端会**轮询** `GET /documents` 直到全部进入 `indexed`/`failed`。后端要保证 status 字段实时更新到数据库。

### 3.4 检索 `POST /knowledge-bases/{kbId}/retrieve`

**请求**:
```json
{
  "query": "如何部署?",
  "top_k": 4,
  "score_threshold": 0.5,
  "hybrid": true,
  "rerank": false
}
```

**响应**:
```json
{
  "code": 0,
  "data": [
    {
      "chunk": {
        "id": "chunk_1",
        "document_id": "doc_1",
        "index": 0,
        "content": "部署步骤如下...",
        "metadata": {"page": 12},
        "token_count": 128
      },
      "document": {
        "id": "doc_1",
        "name": "部署指南.pdf",
        "source_url": null
      },
      "score": 0.87
    }
  ],
  "message": "ok"
}
```

按 `score` 降序排列,数量 ≤ `top_k`。

---

## 四、数据模型(对应 TypeScript 类型)

完整 TS 类型见前端 `src/types/index.ts`,后端的 ORM 模型字段名应**严格保持一致**(snake_case)。关键模型:

### User
```python
class User:
    id: str                    # "user_xxx"
    email: str                 # 唯一
    name: str
    avatar_url: Optional[str]
    role: Literal["owner", "admin", "member", "guest"]
    status: Literal["active", "disabled", "pending"]
    password_hash: str         # 不在 API 中返回
    created_at: datetime
    updated_at: datetime
```

### KnowledgeBase
```python
class KnowledgeBase:
    id: str
    name: str
    description: Optional[str]
    visibility: Literal["private", "workspace", "public"]
    owner_id: str
    collaborators: List[Collaborator]
    chunk_size: int
    chunk_overlap: int
    document_count: int        # 计算字段
    chunk_count: int           # 计算字段
    size_bytes: int            # 计算字段
    created_at: datetime
    updated_at: datetime
```

### KnowledgeDocument
```python
class KnowledgeDocument:
    id: str
    kb_id: str
    name: str
    source: Literal["upload", "url", "api"]
    source_url: Optional[str]
    mime_type: str
    size_bytes: int
    status: Literal["pending", "parsing", "chunking", "embedding", "indexed", "failed"]
    status_message: Optional[str]   # 失败原因
    progress: int                    # 0-100
    chunk_count: int
    embedding_model_id: Optional[str]     # 当前向量索引使用的 models.id
    vector_index_status: Literal["ready", "stale", "rebuilding", "failed"]
    vector_index_error: Optional[str]
    vector_indexed_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    indexed_at: Optional[datetime]
```

### ChatMessage
```python
class ChatMessage:
    id: str
    session_id: str
    role: Literal["user", "assistant", "system"]
    content: str
    status: Literal["pending", "streaming", "done", "error", "aborted"]
    citations: Optional[List[Citation]]
    tool_calls: Optional[List[ToolCall]]
    usage: Optional[Usage]
    parent_id: Optional[str]   # 用于分支对话(后续)
    created_at: datetime
    error_message: Optional[str]
```

---

## 五、权限模型

### 5.1 全局角色

| 角色 | 权限 |
|---|---|
| `owner` | 所有权限,工作空间唯一所有者,不可删除 |
| `admin` | 管理用户、模型,操作所有资源 |
| `member` | 创建自己的资源,访问公共资源 |
| `guest` | 仅只读访问公开资源 |

### 5.2 资源级权限

每个知识库/Agent 都有:
- `owner_id`:创建者,拥有 admin 权限
- `visibility`:`private`(仅 owner 可见) / `workspace`(团队可见) / `public`(所有人可见)
- `collaborators`:协作者列表,每个有 `read`/`write`/`admin` 权限

**鉴权伪代码**:
```python
def can_access(user, resource, action):
    # 全局 admin/owner 直通
    if user.role in ("owner", "admin"):
        return True
    # 资源 owner 直通
    if resource.owner_id == user.id:
        return True
    # 协作者
    collab = find(resource.collaborators, user_id=user.id)
    if collab:
        return permission_order[collab.permission] >= permission_order[action]
    # 公开资源仅允许 read
    if resource.visibility == "public" and action == "read":
        return True
    if resource.visibility == "workspace" and action == "read":
        return True
    return False
```

---

## 六、安全要求

1. **密码存储**:bcrypt / argon2,不要 MD5/SHA
2. **JWT 密钥**:足够长的随机字符串,不要 commit 到代码库
3. **SQL 注入防护**:使用 ORM 或参数化查询
4. **文件上传**:
   - 限制大小(建议 50MB)
   - 校验 MIME type 和扩展名
   - 文件存储与代码目录隔离
5. **CORS**:开发环境允许前端域名,生产环境严格白名单
6. **速率限制**:`/auth/login` 必须有(防爆破),`/chat/completions` 也建议有
7. **审计日志**:登录、关键操作(删库、改权限)记日志

---

## 七、推荐技术栈(参考)

| 层 | 选型 |
|---|---|
| Web 框架 | FastAPI |
| ORM | SQLAlchemy 2.0 (async) |
| DB | PostgreSQL |
| 向量库 | pgvector / Milvus / Qdrant |
| 队列 | Celery / Arq(用于文档解析) |
| 缓存 | Redis(refresh token 黑名单、限流) |
| 模型 | Ollama / vLLM(本地) |
| 嵌入 | bge-m3 / text-embedding-3 |

---

## 八、开发对接流程

1. 后端按本规范实现接口,先不接 LLM,**用 mock 数据返回**
2. 前端把 `.env.local` 设为 `VITE_USE_MOCK=false` + 后端地址
3. 联调时优先打通认证 → 知识库 CRUD → 文档上传 → SSE 对话
4. 真正接 LLM 之前,SSE 接口可以先用**固定脚本** mock(参考前端 `src/mocks/handlers/chat.ts` 的实现思路)
5. **抓 SSE 流**测试:浏览器 DevTools 的 Network → 选请求 → EventStream 标签页可以实时看每个事件
