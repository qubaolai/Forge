// import { http, HttpResponse, delay } from 'msw';
// import { ChatSession, Citation, ChatMessage, SSEEvent } from '@/types';
// import { db, transact } from '../db';
// import { fail, getPageParams, ok, paginate, requireAuth } from '../utils';

// const BASE = '/api/v1';

// export const sessionHandlers = [
//   // // 会话列表
//   // http.get(`${BASE}/sessions`, async ({ request }) => {
//   //   const r = await requireAuth(request);
//   //   if (r instanceof HttpResponse) return r;
//   //   const { page, page_size } = getPageParams(new URL(request.url));
//   //   const list = db.get().sessions
//   //     .filter((s) => s.user_id === r.user.id)
//   //     .sort((a, b) => (b.updated_at).localeCompare(a.updated_at));
//   //   return ok(paginate(list, page, page_size));
//   // }),

//   // // 创建会话
//   // http.post(`${BASE}/sessions`, async ({ request }) => {
//   //   const r = await requireAuth(request);
//   //   if (r instanceof HttpResponse) return r;
//   //   const { agent_id, title } = (await request.json()) as { agent_id: string; title?: string };
//   //   const data = db.get();
//   //   const agent = data.agents.find((a) => a.id === 'agent_demo');
//   //   if (!agent) return fail(40404, 'Agent 不存在', 404);

//   //   const created = transact((d) => {
//   //     const s: ChatSession = {
//   //       id: db.uid('sess'),
//   //       title: title || '新对话',
//   //       agent_id,
//   //       agent_name: agent.name,
//   //       user_id: r.user.id,
//   //       message_count: 0,
//   //       created_at: db.now(),
//   //       updated_at: db.now(),
//   //     };
//   //     d.sessions.push(s);
//   //     return s;
//   //   });
//   //   return ok(created);
//   // }),

//   // // 会话详情
//   // http.get(`${BASE}/sessions/:id`, async ({ request, params }) => {
//   //   const r = await requireAuth(request);
//   //   if (r instanceof HttpResponse) return r;
//   //   const s = db.get().sessions.find((x) => x.id === params.id && x.user_id === r.user.id);
//   //   if (!s) return fail(40404, '会话不存在', 404);
//   //   return ok(s);
//   // }),

//   // // 重命名
//   // http.patch(`${BASE}/sessions/:id`, async ({ request, params }) => {
//   //   const r = await requireAuth(request);
//   //   if (r instanceof HttpResponse) return r;
//   //   const patch = (await request.json()) as { title?: string };
//   //   const updated = transact((d) => {
//   //     const s = d.sessions.find((x) => x.id === params.id && x.user_id === r.user.id);
//   //     if (!s) return null;
//   //     if (patch.title) s.title = patch.title;
//   //     s.updated_at = db.now();
//   //     return s;
//   //   });
//   //   if (!updated) return fail(40404, '会话不存在', 404);
//   //   return ok(updated);
//   // }),

//   // // 删除会话
//   // http.delete(`${BASE}/sessions/:id`, async ({ request, params }) => {
//   //   const r = await requireAuth(request);
//   //   if (r instanceof HttpResponse) return r;
//   //   transact((d) => {
//   //     d.sessions = d.sessions.filter((x) => x.id !== params.id);
//   //     d.messages = d.messages.filter((m) => m.session_id !== params.id);
//   //   });
//   //   return ok(null);
//   // }),

//   // // 历史消息
//   // http.get(`${BASE}/sessions/:id/messages`, async ({ request, params }) => {
//   //   const r = await requireAuth(request);
//   //   if (r instanceof HttpResponse) return r;
//   //   const { page, page_size } = getPageParams(new URL(request.url));
//   //   const list = db.get().messages
//   //     .filter((m) => m.session_id === params.id)
//   //     .sort((a, b) => a.created_at.localeCompare(b.created_at));
//   //   return ok(paginate(list, page, page_size));
//   // }),
// ];

// // ---- SSE 流式接口 ----
// export const chatHandlers = [
//   http.post(`${BASE}/chat/completions`, async ({ request }) => {
//     const r = await requireAuth(request);
//     if (r instanceof HttpResponse) return r;

//     const { session_id, message } = (await request.json()) as {
//       session_id: string;
//       message: string;
//     };

//     const data = db.get();
//     const session = data.sessions.find((s) => s.id === session_id);
//     if (!session) return fail(40404, '会话不存在', 404);

//     // 写入用户消息
//     const userMsg: ChatMessage = {
//       id: db.uid('msg'),
//       session_id,
//       role: 'user',
//       content: message,
//       status: 'done',
//       created_at: db.now(),
//     };
//     transact((d) => {
//       d.messages.push(userMsg);
//       const s = d.sessions.find((x) => x.id === session_id);
//       if (s) {
//         s.message_count++;
//         s.last_message_at = db.now();
//         s.updated_at = db.now();
//         // 用第一条用户消息更新标题
//         if (s.message_count === 1) s.title = message.slice(0, 20);
//       }
//     });

//     // 构造 assistant 应答(模拟 RAG:给出引用 + 文本流)
//     const assistantId = db.uid('msg');
//     const replyText = generateReply(message);
//     const citations = generateCitations(session.agent_id);

//     const stream = new ReadableStream({
//       async start(controller) {
//         const encoder = new TextEncoder();
//         const send = (event: SSEEvent) => {
//           controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`));
//         };

//         await delay(150);
//         send({ type: 'message_start', message_id: assistantId, session_id });

//         // 模拟"检索中"工具调用
//         await delay(300);
//         const toolCallId = 'tc_' + Date.now();
//         send({
//           type: 'tool_call',
//           tool_call: {
//             id: toolCallId,
//             tool_id: 'retrieval',
//             tool_name: '知识库检索',
//             arguments: { query: message },
//             status: 'running',
//           },
//         });
//         await delay(500);
//         send({
//           type: 'tool_result',
//           tool_call_id: toolCallId,
//           status: 'success',
//           result: { matched: citations.length },
//         });

//         // 推送引用
//         send({ type: 'citations', citations });

//         // 文本逐字流式
//         for (const chunk of chunkText(replyText, 4)) {
//           await delay(40);
//           send({ type: 'delta', content: chunk });
//         }

//         await delay(100);
//         send({
//           type: 'done',
//           finish_reason: 'stop',
//           usage: { prompt_tokens: 120, completion_tokens: replyText.length, total_tokens: 120 + replyText.length },
//         });

//         // 写入完整 assistant 消息
//         transact((d) => {
//           d.messages.push({
//             id: assistantId,
//             session_id,
//             role: 'assistant',
//             content: replyText,
//             status: 'done',
//             citations,
//             created_at: db.now(),
//           });
//           const s = d.sessions.find((x) => x.id === session_id);
//           if (s) {
//             s.message_count++;
//             s.last_message_at = db.now();
//             s.updated_at = db.now();
//           }
//         });

//         controller.close();
//       },
//     });

//     return new HttpResponse(stream, {
//       headers: {
//         'Content-Type': 'text/event-stream',
//         'Cache-Control': 'no-cache',
//         'Connection': 'keep-alive',
//       },
//     });
//   }),

//   // 中断(mock 中无实际流可断,直接返回 OK)
//   http.post(`${BASE}/chat/stop`, () => ok(null)),

//   http.post(`${BASE}/chat/regenerate`, async ({ request }) => {
//     const r = await requireAuth(request);
//     if (r instanceof HttpResponse) return r;
//     return ok({ new_message_id: db.uid('msg') });
//   }),
// ];

// // ---- 辅助 ----
// function generateReply(query: string): string {
//   // 简单根据 query 关键字给固定回复,真实开发时无所谓内容
//   if (query.includes('v2') || query.includes('版本') || query.includes('功能')) {
//     return 'v2.0 版本主要带来了三项新能力:多模态输入支持[1]、本地模型接入[2],以及离线部署能力[3]。这些更新让产品能够更好地适配企业内网场景。';
//   }
//   if (query.includes('部署') || query.includes('安装')) {
//     return '部署主要分为三步:准备环境(Docker 或 Python 3.10+)、下载模型权重、启动服务。详细步骤请参考部署指南[1]。';
//   }
//   return `这是针对"${query}"的模拟回答。在真实场景中,助手会基于知识库内容生成回答并标注引用来源[1]。`;
// }

// function generateCitations(_agentId: string): Citation[] {
//   const data = db.get();
//   // 取知识库 demo 的前几个 chunk 作为引用
//   const chunks = data.chunks.slice(0, 3);
//   return chunks.map((c, i) => {
//     const doc = data.documents.find((d) => d.id === c.document_id)!;
//     return {
//       index: i + 1,
//       chunk_id: c.id,
//       document_id: doc.id,
//       document_name: doc.name,
//       content: c.content,
//       score: 0.92 - i * 0.05,
//       metadata: c.metadata,
//     };
//   });
// }

// function chunkText(text: string, size: number): string[] {
//   const out: string[] = [];
//   for (let i = 0; i < text.length; i += size) {
//     out.push(text.slice(i, i + size));
//   }
//   return out;
// }
