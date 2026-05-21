import { authHandlers } from './handlers/auth';
import { knowledgeHandlers } from './handlers/knowledge';
import { agentHandlers, toolHandlers, modelHandlers } from './handlers/agent';
// import { sessionHandlers, chatHandlers } from './handlers/chat';
import { userHandlers, auditHandlers } from './handlers/admin';

export const handlers = [
  ...authHandlers,
  ...knowledgeHandlers,
  ...agentHandlers,
  ...toolHandlers,
  ...modelHandlers,
  // ...sessionHandlers,
  // ...chatHandlers,
  ...userHandlers,
  ...auditHandlers,
];
