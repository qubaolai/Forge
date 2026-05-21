import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Search, Filter, FileSearch } from 'lucide-react';
import { auditApi } from '@/api';
import { AuditLog } from '@/types';

export default function AdminAuditPage() {
  const [page, setPage] = useState(1);
  const [userIdFilter, setUserIdFilter] = useState('');
  const [actionFilter, setActionFilter] = useState('');
  const [selected, setSelected] = useState<AuditLog | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ['admin-audit', page, userIdFilter, actionFilter],
    queryFn: () =>
      auditApi.list({
        page,
        page_size: 20,
        user_id: userIdFilter || undefined,
        action: actionFilter || undefined,
      }),
  });

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-6xl mx-auto px-8 py-8">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-xl font-semibold">审计日志</h1>
            <p className="text-sm text-gray-500 mt-1">
              系统关键操作记录(登录、用户管理、Agent 变更等)
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2 mb-4">
          <div className="relative flex-1 max-w-xs">
            <Search
              size={14}
              className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400"
            />
            <input
              value={userIdFilter}
              onChange={(e) => {
                setUserIdFilter(e.target.value);
                setPage(1);
              }}
              placeholder="按用户 ID 过滤"
              className="w-full pl-8 pr-3 py-1.5 text-sm border rounded-md outline-none focus:border-gray-400"
            />
          </div>
          <div className="relative flex-1 max-w-xs">
            <Filter
              size={14}
              className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400"
            />
            <input
              value={actionFilter}
              onChange={(e) => {
                setActionFilter(e.target.value);
                setPage(1);
              }}
              placeholder="按 action 过滤(如 user.login)"
              className="w-full pl-8 pr-3 py-1.5 text-sm border rounded-md outline-none focus:border-gray-400"
            />
          </div>
        </div>

        <div className="bg-white border rounded-lg overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-gray-500 text-xs">
              <tr>
                <th className="text-left font-medium px-4 py-2.5">时间</th>
                <th className="text-left font-medium px-4 py-2.5">用户</th>
                <th className="text-left font-medium px-4 py-2.5">Action</th>
                <th className="text-left font-medium px-4 py-2.5">资源</th>
                <th className="text-left font-medium px-4 py-2.5">IP</th>
              </tr>
            </thead>
            <tbody>
              {isLoading && (
                <tr>
                  <td colSpan={5} className="text-center text-gray-400 py-12">
                    加载中…
                  </td>
                </tr>
              )}
              {!isLoading && data?.items.length === 0 && (
                <tr>
                  <td colSpan={5} className="text-center text-gray-400 py-12">
                    <FileSearch className="mx-auto text-gray-300 mb-2" size={32} />
                    暂无日志
                  </td>
                </tr>
              )}
              {data?.items.map((log) => (
                <tr
                  key={log.id}
                  onClick={() => setSelected(log)}
                  className="border-t hover:bg-gray-50 cursor-pointer"
                >
                  <td className="px-4 py-2.5 text-xs text-gray-600 whitespace-nowrap">
                    {new Date(log.created_at).toLocaleString()}
                  </td>
                  <td className="px-4 py-2.5">
                    {log.user_name || (
                      <span className="text-gray-400">-</span>
                    )}
                    {log.user_id && (
                      <span className="text-[10px] text-gray-400 ml-1">
                        {log.user_id.slice(0, 8)}
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-2.5">
                    <code className="text-xs bg-gray-100 px-1.5 py-0.5 rounded">
                      {log.action}
                    </code>
                  </td>
                  <td className="px-4 py-2.5 text-xs text-gray-600">
                    {log.resource_type}
                    {log.resource_id && (
                      <span className="text-gray-400">/{log.resource_id}</span>
                    )}
                  </td>
                  <td className="px-4 py-2.5 text-xs text-gray-500 font-mono">
                    {log.ip_address || '-'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {data && data.total > data.page_size && (
          <div className="flex items-center justify-between mt-3 text-xs text-gray-500">
            <span>
              共 {data.total} 条 · 第 {data.page} /{' '}
              {Math.ceil(data.total / data.page_size)} 页
            </span>
            <div className="flex gap-2">
              <button
                disabled={page <= 1}
                onClick={() => setPage((p) => p - 1)}
                className="px-2 py-1 border rounded disabled:opacity-40"
              >
                上一页
              </button>
              <button
                disabled={page >= Math.ceil(data.total / data.page_size)}
                onClick={() => setPage((p) => p + 1)}
                className="px-2 py-1 border rounded disabled:opacity-40"
              >
                下一页
              </button>
            </div>
          </div>
        )}
      </div>

      {selected && <DetailDialog log={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}

function DetailDialog({ log, onClose }: { log: AuditLog; onClose: () => void }) {
  return (
    <div
      onClick={onClose}
      className="fixed inset-0 z-50 bg-black/30 flex items-center justify-center p-4"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="bg-white rounded-lg shadow-xl w-full max-w-lg p-5 max-h-[90vh] overflow-y-auto"
      >
        <h2 className="text-base font-semibold mb-3">日志详情</h2>
        <dl className="grid grid-cols-3 gap-x-3 gap-y-2 text-sm">
          <dt className="text-gray-500 text-xs">ID</dt>
          <dd className="col-span-2 font-mono text-xs">{log.id}</dd>
          <dt className="text-gray-500 text-xs">时间</dt>
          <dd className="col-span-2">{new Date(log.created_at).toLocaleString()}</dd>
          <dt className="text-gray-500 text-xs">用户</dt>
          <dd className="col-span-2">
            {log.user_name} <span className="text-gray-400 text-xs">{log.user_id}</span>
          </dd>
          <dt className="text-gray-500 text-xs">Action</dt>
          <dd className="col-span-2 font-mono">{log.action}</dd>
          <dt className="text-gray-500 text-xs">资源</dt>
          <dd className="col-span-2">
            {log.resource_type}{' '}
            {log.resource_id && (
              <span className="text-gray-400">/ {log.resource_id}</span>
            )}
          </dd>
          <dt className="text-gray-500 text-xs">IP</dt>
          <dd className="col-span-2 font-mono text-xs">{log.ip_address || '-'}</dd>
          <dt className="text-gray-500 text-xs">User-Agent</dt>
          <dd className="col-span-2 text-xs break-all">{log.user_agent || '-'}</dd>
        </dl>
        {log.metadata && Object.keys(log.metadata).length > 0 && (
          <div className="mt-3">
            <div className="text-gray-500 text-xs mb-1">Metadata</div>
            <pre className="bg-gray-50 border rounded p-2 text-[11px] overflow-x-auto">
              {JSON.stringify(log.metadata, null, 2)}
            </pre>
          </div>
        )}
        <div className="flex justify-end mt-4">
          <button
            onClick={onClose}
            className="px-3 py-1.5 text-sm border rounded-md hover:bg-gray-50"
          >
            关闭
          </button>
        </div>
      </div>
    </div>
  );
}
