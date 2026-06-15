// ============================================================================
// 通用基础类型
// ============================================================================

/** 后端统一响应格式 */
export interface ApiResponse<T = unknown> {
  code: number;
  data: T;
  message: string;
  details?: Record<string, unknown>;
}

/** 分页响应 */
export interface PaginatedData<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

/** 分页查询参数 */
export interface PaginationParams {
  page?: number;
  page_size?: number;
  q?: string;
}

/** ISO 8601 时间字符串 */
export type ISODateString = string;

// ============================================================================
// 用户与权限
// ============================================================================

export type UserRole = 'owner' | 'admin' | 'member' | 'guest';
export type UserStatus = 'active' | 'disabled' | 'pending';

export interface User {
  id: string;
  email: string;
  name: string;
  avatar_url?: string;
  role: UserRole;
  status: UserStatus;
  created_at: ISODateString;
  updated_at: ISODateString;
}

export interface AuthTokens {
  access_token: string;
  expires_at: ISODateString; // access token 过期时间
}

export interface LoginPayload {
  email: string;
  password: string;
}

export interface LoginResponse extends AuthTokens {
  user: User;
}

/** 资源可见性 */
export type Visibility = 'private' | 'workspace' | 'public';

/** 资源协作者权限 */
export type CollaboratorPermission = 'read' | 'write' | 'admin';

export interface Collaborator {
  user_id: string;
  user_name: string;
  permission: CollaboratorPermission;
}

// ============================================================================
// 知识库
// ============================================================================

export type DocumentStatus =
  | 'pending'      // 队列中
  | 'parsing'      // 解析中
  | 'chunking'     // 分块中
  | 'embedding'    // 向量化
  | 'indexed'      // 已索引完成
  | 'failed';      // 失败

export interface KnowledgeBase {
  id: string;
  name: string;
  description?: string;
  visibility: Visibility;
  owner_id: string;
  collaborators: Collaborator[];
  // 配置
  chunk_size: number;
  chunk_overlap: number;
  // 统计
  document_count: number;
  chunk_count: number;
  size_bytes: number;
  created_at: ISODateString;
  updated_at: ISODateString;
}

export type DocumentSource = 'upload' | 'url' | 'api';

export interface KnowledgeDocument {
  id: string;
  kb_id: string;
  name: string;
  source: DocumentSource;
  source_url?: string;
  mime_type: string;
  size_bytes: number;
  status: DocumentStatus;
  status_message?: string;
  progress: number; // 0-100
  chunk_count: number;
  embedding_model_id?: string;
  vector_index_status: 'ready' | 'stale' | 'rebuilding' | 'failed';
  vector_index_error?: string;
  vector_indexed_at?: ISODateString;
  created_at: ISODateString;
  updated_at: ISODateString;
  indexed_at?: ISODateString;
}

export interface DocumentChunk {
  id: string;
  document_id: string;
  index: number;
  content: string;
  metadata: Record<string, unknown>; // page, section, etc.
  token_count: number;
}

export interface RetrievalResult {
  chunk: DocumentChunk;
  document: Pick<KnowledgeDocument, 'id' | 'name' | 'source_url'>;
  score: number;
}

export interface RetrieveRequest {
  query: string;
  top_k?: number;
  score_threshold?: number;
  hybrid?: boolean;
  rerank?: boolean;
}

// ============================================================================
// Agent
// ============================================================================

export interface AgentModelConfig {
  model_id: string;        // 后端模型 endpoint id
  temperature: number;
  max_tokens: number;
  top_p: number;
}

export interface RetrievalConfig {
  kb_ids: string[];
  top_k: number;
  score_threshold: number;
  hybrid: boolean;
  rerank: boolean;
}

export interface ToolBinding {
  tool_id: string;
  enabled: boolean;
  config: Record<string, unknown>; // 由 tool 的 JSON Schema 校验
}

export interface AgentAdvancedConfig {
  context_window: number;
  timeout_seconds: number;
  max_retries: number;
  enable_streaming: boolean;
}

export interface Agent {
  id: string;
  name: string;
  description?: string;
  avatar_url?: string;
  visibility: Visibility;
  owner_id: string;
  collaborators: Collaborator[];

  // 配置
  system_prompt: string;
  opening_message?: string;
  model: AgentModelConfig;
  retrieval: RetrievalConfig;
  tools: ToolBinding[];
  advanced: AgentAdvancedConfig;

  created_at: ISODateString;
  updated_at: ISODateString;
}

// ============================================================================
// 工具与模型
// ============================================================================

export interface ToolDefinition {
  id: string;
  name: string;
  description: string;
  category: string;
  // 用 JSON Schema 描述参数,前端用 react-jsonschema-form 等渲染
  parameters_schema: Record<string, unknown>;
  is_dangerous: boolean;
}

export type ModelType = 'chat' | 'embedding' | 'reranker';

export interface ChatModelConfig {
  context_window?: number;
  max_output_tokens?: number;
  input_modalities?: string[];
  output_modalities?: string[];
  capabilities?: string[];
  thinking_options?: string[] | null;
  provider_options?: Record<string, unknown>;
}

export interface ModelInfo {
  provider: string;
  model_id: string;
  name: string;
  display_name: string;
  model_type: ModelType;
  config: ChatModelConfig & Record<string, unknown>;
  thinking: { options?: string[] | null; default?: string | null } | null;
}

export interface ModelGroup {
  provider: string;
  models: ModelInfo[];
}

export interface GroupedModelsResponse {
  groups: ModelGroup[];
  providers?: string[];
  models?: ModelInfo[];
  provider?: string;
  model_type?: ModelType;
}

// ============================================================================
// 管理端 - 供应商 / 模型 / API-Key（供应商中心页）
// ============================================================================

// 供应商下的模型（管理视图）
export interface ProviderModel {
  id: string;          // 雪花 ID 字符串 (与 model_id 同值)
  model_id: string;
  provider_id: string;
  name: string;
  display_name: string;
  model_type: ModelType;
  config: Record<string, unknown>;
  is_enabled: boolean;
}

// 供应商 API-Key（脱敏，绝不含明文）
export interface ProviderKey {
  key_id: string;
  key_fingerprint: string;
  is_enabled: boolean;
  weight: number;
  cooldown_until: string | null;
  failure_score: number;
  last_error_at: string | null;
}

// 供应商（管理视图，含模型与 Key 数量）
export interface ProviderAdmin {
  id: string;          // 雪花 ID 字符串 (与 provider_id 同值)
  provider_id: string;
  name: string;
  impl: string;
  base_url: string | null;
  is_enabled: boolean;
  key_count: number;
  model_count: number;
  models: ProviderModel[];
}

// 模型新增 / 更新载荷（字段全可选，对齐后端 ModelCreateIn / ModelUpdateIn）
export interface ModelUpsert {
  name?: string;
  display_name?: string;
  model_type?: ModelType;
  config?: Record<string, unknown>;
  enabled?: boolean;
}

export interface SystemModelBinding {
  id: string;
  role: 'rag_embedding' | 'semantic_history_embedding' | 'rag_reranker';
  model_id: string | null;
  version: number;
  optional: boolean;
  model: ProviderModel | null;
}

// 模型调用链(对话链 conversation / 档位链 tier)
export type ModelChainScope = 'conversation' | 'tier';

export interface ModelChainEntry {
  provider: string;
  model: string;
}

export interface ModelChain {
  id: string;
  scope: ModelChainScope;
  chain_key: string; // provider 名(conversation)或 fast/smart/strong(tier)
  entries: ModelChainEntry[];
  version: number;
}

export interface RagIndexJob {
  id: string;
  model_id: string;
  binding_version: number;
  status: string;
  total_documents: number;
  succeeded_documents: number;
  failed_documents: number;
  error_message: string | null;
  created_at?: ISODateString;
  updated_at?: ISODateString;
}

export interface RagIndexStatus {
  documents: Partial<Record<KnowledgeDocument['vector_index_status'], number>>;
  jobs: RagIndexJob[];
}

// ============================================================================
// 会话与消息
// ============================================================================

export type MessageRole = 'user' | 'assistant' | 'system';

export type MessageStatus = 'pending' | 'streaming' | 'done' | 'error' | 'aborted' | 'partial';

export interface Citation {
  index: number;          // 引用序号 [1] [2]
  chunk_id: string;
  document_id: string;
  document_name: string;
  content: string;
  score: number;
  metadata?: Record<string, unknown>;
}

export interface ToolCall {
  id: string;
  tool_id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  result?: unknown;
  status: 'running' | 'success' | 'error';
  error_message?: string;
}

export interface MessageUsage {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
}

// 单个上下文层的占用 (与后端 context_meta.layers 对齐)
export interface ContextLayer {
  name: string;            // system_prompt / workspace / facts / summary / dialogue / tool_results / current_input
  token_count: number;
  ratio: number;           // token_count / context_window
  message_count?: number;
  truncated?: boolean;
}

// 一次会话的上下文占用快照 (分层)
export interface ContextUsage {
  context_window: number;
  input_tokens: number;
  total_ratio: number;     // input_tokens / context_window
  layers: ContextLayer[];
}

/** 会话文件 (用户上传附件 source=upload / LLM write_file 生成 source=generated) */
export interface ChatFileMeta {
  id: string;
  name: string;
  source: 'upload' | 'generated';
  size_bytes: number;
  mime_type?: string | null;
}

/** 文件预览返回 (文本切片) */
export interface ChatFilePreview {
  id: string;
  name: string;
  text: string;
  total_lines: number;
  returned_range: number[];
  truncated: boolean;
  mime_type?: string | null;
}

export interface ChatMessage {
  id: string;
  session_id: string;
  role: MessageRole;
  content: string;
  status: MessageStatus;
  citations?: Citation[];
  tool_calls?: ToolCall[];
  usage?: MessageUsage;
  parent_id?: string;     // 用于分支对话
  created_at: ISODateString;
  error_message?: string;
  // 思考链 (DeepSeek thinking / Anthropic extended thinking 等); 仅 assistant 用
  reasoning_content?: string;
  // 思考累计墙钟毫秒, 仅 assistant 用 (DeepSeek thinking 等开启时才有)
  reasoning_duration_ms?: number;
  // 上下文占用快照 (持久化, 仅 assistant 消息有值)
  context_usage?: ContextUsage | null;
  // 关联会话文件 (user=上传附件 / assistant=write_file 生成); 历史回看 + 流式 file_created 累积
  files?: ChatFileMeta[];
}

export interface ChatSession {
  id: string;
  title: string;
  agent_id: string;
  agent_name: string;
  user_id: string;
  message_count: number;
  last_message_at?: ISODateString;
  created_at: ISODateString;
  updated_at: ISODateString;
}

export interface ChatCompletionRequest {
  session_id?: string;
  message: string;
  attachments?: { file_id: string; type: string }[];
  model_options: {
    provider: string;
    model: string;
    thinking?: boolean;
    thinking_level?: 'low' | 'medium' | 'high' | 'xhigh';
  };
}

// ============================================================================
// Adaptive Run
// ============================================================================

export type RunStatus =
  | 'created'
  | 'planning'
  | 'validating'
  | 'executing'
  | 'integrating'
  | 'verifying'
  | 'completed'
  | 'failed'
  | 'blocked'
  | 'aborted';

export type TaskStatus = 'pending' | 'running' | 'completed' | 'failed' | 'skipped';

export type TaskKind = 'read' | 'write' | 'execute' | 'review' | 'integrate';

export type ArtifactKind =
  | 'discovery_report'
  | 'task_graph'
  | 'patch_set'
  | 'review_report'
  | 'test_report'
  | 'integration_report'
  | 'conflict_report'
  | 'final_report';

export interface TaskNode {
  id: string;
  title: string;
  kind: TaskKind;
  allowed_tools: string[];
  read_scope: string[];
  write_scope: string[];
  deps: string[];
  model_profile: 'fast' | 'smart' | 'strong';
  max_steps: number;
  acceptance_criteria: string;
  output_contract: string;
  command?: string | null;
  status: TaskStatus;
  artifact_ids: string[];
  error?: string | null;
  started_at?: ISODateString | null;
  completed_at?: ISODateString | null;
}

export interface TaskGraph {
  nodes: Record<string, TaskNode>;
  planner_raw?: string;
  created_at: ISODateString;
}

export interface AdaptiveRun {
  run_id: string;
  workspace_path: string;
  goal: string;
  status: RunStatus;
  task_graph?: TaskGraph | null;
  current_wave: number;
  replan_count: number;
  artifact_ids: string[];
  owner_user_id: string;
  created_at: ISODateString;
  updated_at: ISODateString;
  metadata: Record<string, unknown>;
  options_snapshot?: Record<string, unknown> | null;
}

export interface AdaptiveArtifact {
  artifact_id: string;
  run_id: string;
  task_id: string | null;
  kind: ArtifactKind;
  payload: unknown;
  created_at: ISODateString;
}

export interface AdaptiveRunEvent {
  id: string;
  run_id: string;
  type: string;
  ts: ISODateString;
  payload: Record<string, unknown>;
}

export interface AdaptiveRunSummary {
  run_id?: string;
  status?: RunStatus;
  artifact_ids: string[];
  events: AdaptiveRunEvent[];
}

export interface AdaptiveRunList {
  items: AdaptiveRun[];
  total: number;
}

export interface AdaptiveArtifactList {
  items: AdaptiveArtifact[];
  total: number;
}

export type RunSSEEvent =
  | AdaptiveRunEvent
  | {
      type: 'stream.closed';
      run_id: string;
      status: RunStatus;
      reason: string;
    };

// ============================================================================
// SSE 事件类型(对应后端 chat/completions 流)
// ============================================================================

export type SSEEvent =
  | { type: 'session_created'; session_id: string; title: string }
  | { type: 'session_renamed'; session_id: string; title: string }
  | { type: 'message_start'; message_id: string; session_id: string }
  | { type: 'message_resumed'; message_id: string; session_id: string; prev_content_len: number; prev_reason: string }
  | { type: 'delta'; content: string }
  | { type: 'reasoning_delta'; content: string }   // 思考链增量 (DeepSeek thinking 等)
  | { type: 'reasoning_end'; reasoning_duration_ms?: number }   // 思考阶段结束
  | { type: 'tool_call'; tool_call: ToolCall }
  | { type: 'tool_result'; tool_call_id: string; result: unknown; status: 'success' | 'error' }
  | { type: 'citations'; citations: Citation[] }
  | { type: 'file_created'; id: string; name: string; source: 'generated' | 'upload'; size_bytes: number; mime_type?: string | null }
  | { type: 'compaction_started'; reason: string; estimated_tokens: number; context_window: number }
  | { type: 'compaction_done'; tokens_saved: number; estimated_tokens: number; rebuild_count: number; ok: boolean }
  | ({ type: 'context_usage' } & ContextUsage)
  | {
      type: 'done';
      usage: MessageUsage;
      finish_reason: 'stop' | 'length' | 'tool_calls' | 'aborted';
      reasoning_duration_ms?: number | null;
    }
  | { type: 'error'; message: string; code?: string }
  | {
      type:
        | 'run.created'
        | 'run.started'
        | 'run.status_changed'
        | 'run.completed'
        | 'run.failed'
        | 'run.blocked'
        | 'run.aborted'
        | 'task.started'
        | 'task.completed'
        | 'task.failed'
        | 'task.skipped'
        | 'wave.started'
        | 'wave.completed'
        | 'artifact.created'
        | 'plan.created'
        | 'plan.validated'
        | 'plan.rejected'
        | 'integration.started'
        | 'integration.completed'
        | 'integration.conflict'
        | 'verify.started'
        | 'verify.passed'
        | 'verify.failed';
      run_id: string;
      event_id?: string;
      ts?: ISODateString;
      payload: Record<string, unknown>;
    }
  | {
      type: 'run.done';
      run_id: string;
      status: RunStatus;
      artifact_ids: string[];
    }
  | {
      type: 'task_partial';
      message_id: string;
      session_id: string;
      reason: string;
      content_so_far: string;
      tool_calls_so_far?: ToolCall[] | null;
      usage?: MessageUsage;
      reasoning_duration_ms?: number | null;
      resumable: boolean;
    };

// ============================================================================
// 审计日志
// ============================================================================

export interface AuditLog {
  id: string;
  user_id: string;
  user_name: string;
  action: string;          // e.g. 'kb.create' / 'agent.update' / 'user.login'
  resource_type: string;
  resource_id?: string;
  ip_address?: string;
  user_agent?: string;
  metadata?: Record<string, unknown>;
  created_at: ISODateString;
}

// ============================================================================
// 错误
// ============================================================================

export class ApiError extends Error {
  constructor(
    public code: number,
    message: string,
    public details?: Record<string, unknown>,
    public httpStatus?: number,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}
