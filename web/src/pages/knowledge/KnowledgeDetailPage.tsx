import { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { ArrowLeft, FileText, Search, Settings as SettingsIcon } from 'lucide-react';
import { kbApi } from '@/api';
import { cn } from '@/lib/utils';
import { VisibilityBadge } from './KnowledgeListPage';
import { DocumentsTab } from '@/components/knowledge/DocumentsTab';
import { RetrievalTab } from '@/components/knowledge/RetrievalTab';
import { KbSettingsTab } from '@/components/knowledge/KbSettingsTab';

type TabKey = 'documents' | 'retrieval' | 'settings';

export default function KnowledgeDetailPage() {
  const { kbId } = useParams<{ kbId: string }>();
  const navigate = useNavigate();
  const [tab, setTab] = useState<TabKey>('documents');

  const { data: kb, isLoading } = useQuery({
    queryKey: ['knowledge-base', kbId],
    queryFn: () => kbApi.get(kbId!),
    enabled: !!kbId,
  });

  if (!kbId) {
    return null;
  }

  return (
    <div className="h-full flex flex-col">
      {/* 顶部 */}
      <div className="px-6 py-4 border-b shrink-0">
        <button
          onClick={() => navigate('/knowledge')}
          className="text-xs text-gray-500 hover:text-gray-900 flex items-center gap-1 mb-2"
        >
          <ArrowLeft size={12} />
          返回知识库列表
        </button>
        {isLoading ? (
          <div className="text-sm text-gray-400">加载中…</div>
        ) : kb ? (
          <div className="flex items-start justify-between">
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-lg font-semibold">{kb.name}</h1>
                <VisibilityBadge visibility={kb.visibility} />
              </div>
              {kb.description && (
                <p className="text-sm text-gray-500 mt-1">{kb.description}</p>
              )}
              <div className="flex items-center gap-4 text-xs text-gray-400 mt-2">
                <span>{kb.document_count} 文档</span>
                <span>{kb.chunk_count} 分块</span>
              </div>
            </div>
          </div>
        ) : (
          <div className="text-sm text-red-500">知识库不存在</div>
        )}

        {/* Tab 栏 */}
        <div className="flex gap-1 mt-4 -mb-4 border-b -mx-6 px-6">
          <TabButton active={tab === 'documents'} onClick={() => setTab('documents')} icon={<FileText size={14} />}>
            文档管理
          </TabButton>
          <TabButton active={tab === 'retrieval'} onClick={() => setTab('retrieval')} icon={<Search size={14} />}>
            检索测试
          </TabButton>
          <TabButton active={tab === 'settings'} onClick={() => setTab('settings')} icon={<SettingsIcon size={14} />}>
            设置
          </TabButton>
        </div>
      </div>

      {/* Tab 内容 */}
      <div className="flex-1 overflow-hidden">
        {tab === 'documents' && <DocumentsTab kbId={kbId} />}
        {tab === 'retrieval' && <RetrievalTab kbId={kbId} />}
        {tab === 'settings' && kb && <KbSettingsTab kb={kb} />}
      </div>
    </div>
  );
}

function TabButton({
  active, onClick, icon, children,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        'flex items-center gap-1.5 px-3 py-2 text-sm border-b-2 transition-colors -mb-px',
        active
          ? 'border-gray-900 text-gray-900 font-medium'
          : 'border-transparent text-gray-500 hover:text-gray-900',
      )}
    >
      {icon}
      {children}
    </button>
  );
}
