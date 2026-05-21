import 'katex/dist/katex.min.css';
import '@/styles/code-theme.css';

import { Check, Copy } from 'lucide-react';
import { useMemo, useState } from 'react';
import ReactMarkdown, { type Components } from 'react-markdown';
import rehypeHighlight from 'rehype-highlight';
import rehypeKatex from 'rehype-katex';
import rehypeRaw from 'rehype-raw';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';

import type { Citation } from '@/types';

interface Props {
  content: string;
  citations?: Citation[];
  onCitationClick?: (citation: Citation) => void;
}

/**
 * 把 [1] [2] 等引用角标转成 <sup data-citation="1">[1]</sup>,
 * 后续 rehype-raw 解析, components.sup 渲染成可点击徽标.
 */
function preprocessCitations(text: string, citations?: Citation[]): string {
  if (!citations || citations.length === 0) return text;
  const validIndices = new Set(citations.map((c) => c.index));
  return text.replace(/\[(\d+)\]/g, (match, num) => {
    const idx = Number(num);
    if (validIndices.has(idx)) {
      return `<sup data-citation="${idx}">[${idx}]</sup>`;
    }
    return match;
  });
}

/**
 * 代码块: 标题栏 (Mac 风格三色灯 + 语言徽标 + 复制按钮) + 代码区 (atom-one-dark 主题).
 * react-markdown 把 ```lang\ncode``` 解析为 <pre><code class="language-lang">...
 */
const LANG_LABELS: Record<string, string> = {
  js: 'JavaScript',
  jsx: 'JavaScript',
  ts: 'TypeScript',
  tsx: 'TypeScript',
  py: 'Python',
  python: 'Python',
  rb: 'Ruby',
  go: 'Go',
  rs: 'Rust',
  rust: 'Rust',
  java: 'Java',
  kt: 'Kotlin',
  c: 'C',
  cpp: 'C++',
  'c++': 'C++',
  cs: 'C#',
  csharp: 'C#',
  php: 'PHP',
  sh: 'Shell',
  bash: 'Bash',
  zsh: 'Zsh',
  ps1: 'PowerShell',
  sql: 'SQL',
  json: 'JSON',
  yaml: 'YAML',
  yml: 'YAML',
  toml: 'TOML',
  xml: 'XML',
  html: 'HTML',
  css: 'CSS',
  scss: 'SCSS',
  md: 'Markdown',
  markdown: 'Markdown',
  dockerfile: 'Dockerfile',
  docker: 'Dockerfile',
  diff: 'Diff',
};

function CodeBlock({ children }: { children: React.ReactNode }) {
  const [copied, setCopied] = useState(false);

  // children 是 react-markdown 传入的 <code class="language-xxx">...</code>
  const codeNode = (Array.isArray(children) ? children[0] : children) as
    | { props?: { className?: string; children?: React.ReactNode } }
    | undefined;
  const className = codeNode?.props?.className ?? '';
  const langMatch = /language-([\w+-]+)/.exec(className);
  const langKey = (langMatch?.[1] ?? '').toLowerCase();
  const langLabel = LANG_LABELS[langKey] || langKey || 'plaintext';
  const codeText = String(codeNode?.props?.children ?? '').replace(/\n$/, '');

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(codeText);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // ignore
    }
  };

  return (
    <div className="not-prose my-4 overflow-hidden rounded-lg border border-gray-200 bg-white">
      {/* 顶栏: OpenAI 风格暗色标题栏 */}
      <div className="flex items-center justify-between bg-[#202123] px-4 py-2">
        <span className="font-sans text-[12px] text-gray-300">{langLabel}</span>
        <button
          type="button"
          onClick={handleCopy}
          className="flex items-center gap-1.5 rounded font-sans text-[12px] text-gray-400
            transition hover:text-white"
          title="复制代码"
        >
          {copied ? (
            <>
              <Check size={14} className="text-emerald-400" />
              <span className="text-emerald-400">已复制</span>
            </>
          ) : (
            <Copy size={14} />
          )}
        </button>
      </div>
      {/* 代码区: 白底 */}
      <pre
        className="!my-0 overflow-x-auto !rounded-none !bg-white px-4 py-4
          text-[14px] leading-[1.65]
          [&_*]:!border-0 [&_*]:!bg-transparent
          [&::-webkit-scrollbar]:h-1.5
          [&::-webkit-scrollbar-thumb]:rounded-full
          [&::-webkit-scrollbar-thumb]:bg-gray-300
          [&::-webkit-scrollbar-track]:bg-transparent"
      >
        {children}
      </pre>
    </div>
  );
}

export function MarkdownContent({ content, citations, onCitationClick }: Props) {
  const processed = useMemo(() => preprocessCitations(content, citations), [content, citations]);
  const citationMap = useMemo(() => {
    const m = new Map<number, Citation>();
    citations?.forEach((c) => m.set(c.index, c));
    return m;
  }, [citations]);

  const components: Components = useMemo(
    () => ({
      pre: ({ children }) => <CodeBlock>{children}</CodeBlock>,

      // 引用角标 (沿用原逻辑)
      sup: ({ node, ...props }) => {
        const idx = Number(
          (node?.properties as Record<string, unknown> | undefined)?.['dataCitation'],
        );
        const citation = citationMap.get(idx);
        if (!citation) return <sup {...props} />;
        return (
          <sup
            className="ml-0.5 inline-flex h-4 min-w-[16px] cursor-pointer items-center justify-center
              rounded bg-blue-50 px-1 text-[10px] font-medium text-blue-600 no-underline transition-colors
              hover:bg-blue-100"
            onClick={(e) => {
              e.preventDefault();
              onCitationClick?.(citation);
            }}
          >
            {idx}
          </sup>
        );
      },

      // GFM 表格
      table: ({ children }) => (
        <div className="my-3 overflow-x-auto">
          <table className="min-w-full border-collapse border border-gray-200 text-sm">
            {children}
          </table>
        </div>
      ),
      th: ({ children }) => (
        <th className="border border-gray-200 bg-gray-50 px-3 py-1.5 text-left font-medium">
          {children}
        </th>
      ),
      td: ({ children }) => (
        <td className="border border-gray-200 px-3 py-1.5">{children}</td>
      ),

      // 行内代码 / 代码块共用 code: 块级保留 highlight class, 行内单独样式
      code: ({ className, children, ...rest }) => {
        const isBlock = (className ?? '').startsWith('language-');
        if (isBlock) {
          return (
            <code className={className} {...rest}>
              {children}
            </code>
          );
        }
        return (
          <code
            className="rounded-md border border-gray-200/70 bg-gray-50 px-1.5 py-[1px]
              font-mono text-[0.85em] text-[#d63384]"
            {...rest}
          >
            {children}
          </code>
        );
      },

      // 链接新窗口 + 安全
      a: ({ children, href, ...rest }) => (
        <a
          {...rest}
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          className="text-blue-600 hover:underline"
        >
          {children}
        </a>
      ),
    }),
    [citationMap, onCitationClick],
  );

  return (
    <div
      className="prose max-w-none
        text-[15px] leading-7 text-gray-900
        prose-headings:mt-5 prose-headings:mb-2.5 prose-headings:font-semibold
        prose-h1:text-[1.5em] prose-h2:text-[1.25em] prose-h3:text-[1.1em]
        prose-p:my-3 prose-p:leading-7
        prose-ul:my-3 prose-ol:my-3 prose-li:my-1
        prose-blockquote:border-l-4 prose-blockquote:border-gray-200
        prose-blockquote:bg-gray-50/60 prose-blockquote:py-0.5 prose-blockquote:px-3
        prose-blockquote:not-italic prose-blockquote:text-gray-700
        prose-strong:text-gray-900
        prose-code:before:hidden prose-code:after:hidden
        prose-hr:my-6"
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeRaw, rehypeKatex, rehypeHighlight]}
        skipHtml={false}
        components={components}
      >
        {processed}
      </ReactMarkdown>
    </div>
  );
}
