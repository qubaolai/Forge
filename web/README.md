# RAG Agent Frontend

本地 RAG Agent 项目前端,前后端分离。已内置 **MSW Mock 层**,前端可独立开发,无需后端。

## 启动

```bash
# 1. 安装依赖
npm install

# 2. 生成 MSW Service Worker 到 public 目录(只需做一次)
npx msw init public/ --save

# 3. 启动开发服务器
npm run dev
```

打开 http://localhost:5173,登录信息:

- 管理员:`owner@example.com` / `admin123`
- 普通成员:`member@example.com` / `member123`

## 关闭 Mock(接入真实后端)

在项目根目录创建 `.env.local`:

```bash
VITE_USE_MOCK=false
VITE_API_BASE_URL=http://localhost:8000/api/v1
```

重启 dev server。代码层完全无需修改。

## 技术栈

- Vite + React 18 + TypeScript
- Tailwind CSS + shadcn/ui
- React Router v6 + Zustand + TanStack Query
- React Hook Form + Zod
- Axios + 自封装 SSE 客户端
- MSW(Mock Service Worker)

## 目录结构

```
src/
├── api/                    # 接口层
│   ├── client.ts           # axios + 拦截器(401 自动刷新)
│   ├── sse.ts              # SSE 流式封装
│   └── index.ts            # 各模块 API 函数
├── components/
│   ├── ui/                 # shadcn 组件
│   └── common/             # 通用业务组件(AppLayout 等)
├── hooks/
│   ├── usePermission.tsx   # 权限 + 路由守卫
│   └── useChatStream.ts    # 流式对话 hook
├── mocks/                  # MSW mock 层
│   ├── browser.ts          # worker 启动入口
│   ├── db.ts               # 内存数据库 + localStorage 持久化
│   ├── handlers.ts         # 汇总
│   ├── handlers/           # 按模块拆分的 handlers
│   │   ├── auth.ts
│   │   ├── knowledge.ts
│   │   ├── agent.ts
│   │   ├── chat.ts         # 含 SSE 流式
│   │   └── admin.ts
│   └── utils.ts            # ok/fail/分页等工具
├── pages/                  # 路由页面(目前为占位)
├── store/                  # Zustand stores
├── types/                  # TS 类型定义
├── App.tsx
└── main.tsx                # 入口,启动时加载 MSW
```

## Mock 数据特性

- **持久化**:数据保存在 `localStorage` 的 `rag-mock-db` 键,刷新页面不丢
- **真实流式**:`/chat/completions` 用 `ReadableStream` 模拟 SSE,有 `tool_call` `citations` `delta` `done` 完整事件序列
- **解析进度模拟**:上传文档后会异步走 `parsing → chunking → embedding → indexed` 状态,共约 5 秒
- **检索打分**:基于关键词命中给出模拟相似度分数

### 重置 Mock 数据

浏览器控制台执行:

```js
localStorage.removeItem('rag-mock-db');
location.reload();
```

或者代码里调用 `db.reset()`。

## 关键设计

### 认证
- access token:内存(Zustand 不持久化)
- refresh token:httpOnly cookie(后端设置)
- access 401 时,axios 拦截器自动调 `/auth/refresh` 续期

### SSE 流式
- 不用原生 `EventSource`(不支持自定义 header)
- 用 fetch + ReadableStream 实现,支持 Authorization

### 权限
- 全局角色 + 资源所有权 + 协作者权限
- `usePermission()` / `<RequireAuth>` / `<RequireRole>`

## 待开发

- ⬜ 各 page 实际实现(目前为占位)
- ⬜ shadcn/ui 组件初始化(`npx shadcn-ui@latest init`)
- ⬜ i18n 词条
- ⬜ 单测(Vitest)
