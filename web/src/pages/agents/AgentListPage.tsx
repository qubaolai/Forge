import { useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import * as DropdownMenu from '@radix-ui/react-dropdown-menu';
import {
  Plus, Bot, MoreHorizontal, Copy, Trash2, MessageSquare,
} from 'lucide-react';
import { agentsApi } from '@/api';
import { confirm } from '@/components/common/ConfirmDialog';
import { Agent } from '@/types';
import { VisibilityBadge } from '@/pages/knowledge/KnowledgeListPage';

export default function AgentListPage() {
  const navigate = useNavigate();
  const { data, isLoading } = useQuery({
    queryKey: ['agents'],
    queryFn: () => agentsApi.list({ page: 1, page_size: 50 }),
  });

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-5xl mx-auto px-8 py-8">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-xl font-semibold">Agent</h1>
            <p className="text-sm text-gray-500 mt-1">
              配置不同场景的 RAG 助手,组合知识库与工具
            </p>
          </div>
          <button
            onClick={() => navigate('/agents/new')}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm
              bg-black text-white rounded-md hover:bg-gray-800 transition-colors"
          >
            <Plus size={14} />
            新建 Agent
          </button>
        </div>

        {isLoading ? (
          <div className="text-sm text-gray-400 py-12 text-center">加载中…</div>
        ) : !data || data.items.length === 0 ? (
          <EmptyState onCreate={() => navigate('/agents/new')} />
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {data.items.map((a) => <AgentCard key={a.id} agent={a} />)}
          </div>
        )}
      </div>
    </div>
  );
}

function EmptyState({ onCreate }: { onCreate: () => void }) {
  return (
    <div className="border-2 border-dashed border-gray-200 rounded-lg py-16 text-center">
      <Bot className="mx-auto text-gray-300" size={40} />
      <p className="mt-3 text-sm text-gray-500">还没有 Agent</p>
      <button onClick={onCreate} className="mt-4 text-sm text-blue-600 hover:underline">
        创建第一个 Agent
      </button>
    </div>
  );
}

function AgentCard({ agent }: { agent: Agent }) {
  const navigate = useNavigate();
  const qc = useQueryClient();

  const dupMutation = useMutation({
    mutationFn: () => agentsApi.duplicate(agent.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['agents'] }),
  });

  const delMutation = useMutation({
    mutationFn: () => agentsApi.remove(agent.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['agents'] }),
  });

  return (
    <div className="group border border-gray-200 rounded-lg p-4 bg-white
      hover:border-gray-400 hover:shadow-sm transition-all cursor-pointer"
      onClick={() => navigate(`/agents/${agent.id}`)}
    >
      <div className="flex items-start justify-between mb-2">
        <div className="w-9 h-9 rounded-lg bg-purple-50 flex items-center justify-center">
          <Bot className="text-purple-600" size={18} />
        </div>
        <div className="flex items-center gap-1.5">
          <VisibilityBadge visibility={agent.visibility} />
          <DropdownMenu.Root>
            <DropdownMenu.Trigger asChild>
              <button
                onClick={(e) => e.stopPropagation()}
                className="p-1 rounded text-gray-400 hover:text-gray-700 hover:bg-gray-100"
              >
                <MoreHorizontal size={14} />
              </button>
            </DropdownMenu.Trigger>
            <DropdownMenu.Portal>
              <DropdownMenu.Content
                align="end"
                sideOffset={4}
                onClick={(e) => e.stopPropagation()}
                className="z-50 min-w-[140px] bg-white border rounded-md shadow-md py-1 text-sm"
              >
                <MenuItem onSelect={() => {
                  navigate(`/chat/new?agent=${agent.id}`);
                }} icon={<MessageSquare size={13} />}>开始对话</MenuItem>
                <MenuItem onSelect={() => dupMutation.mutate()} icon={<Copy size={13} />}>
                  复制
                </MenuItem>
                <MenuItem
                  danger
                  onSelect={async () => {
                    if (await confirm({ message: `删除 Agent「${agent.name}」?`, confirmLabel: '删除', danger: true })) delMutation.mutate();
                  }}
                  icon={<Trash2 size={13} />}
                >
                  删除
                </MenuItem>
              </DropdownMenu.Content>
            </DropdownMenu.Portal>
          </DropdownMenu.Root>
        </div>
      </div>
      <h3 className="font-medium text-sm mb-1 truncate">{agent.name}</h3>
      <p className="text-xs text-gray-500 mb-3 line-clamp-2 min-h-[2rem]">
        {agent.description || '无描述'}
      </p>
      <div className="flex items-center gap-2 text-[11px] text-gray-400">
        <span>知识库 {agent.retrieval.kb_ids.length}</span>
        <span>·</span>
        <span>工具 {agent.tools.filter((t) => t.enabled).length}</span>
        <span>·</span>
        <span className="truncate">temp {agent.model.temperature}</span>
      </div>
    </div>
  );
}

function MenuItem({
  onSelect, icon, danger, children,
}: {
  onSelect: () => void;
  icon: React.ReactNode;
  danger?: boolean;
  children: React.ReactNode;
}) {
  return (
    <DropdownMenu.Item
      onSelect={onSelect}
      className={`flex items-center gap-2 px-3 py-1.5 cursor-pointer outline-none
        ${danger ? 'text-red-600 hover:bg-red-50 data-[highlighted]:bg-red-50'
                 : 'hover:bg-gray-100 data-[highlighted]:bg-gray-100'}`}
    >
      {icon}
      {children}
    </DropdownMenu.Item>
  );
}
