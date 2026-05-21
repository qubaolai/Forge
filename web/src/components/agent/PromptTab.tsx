import { AgentDraft } from './draft';
import { Field, TextArea, TextInput } from './FormControls';

interface Props {
  draft: AgentDraft;
  update: (patch: Partial<AgentDraft>) => void;
}

const TEMPLATES = [
  {
    name: '问答助手',
    prompt: '你是一个智能助手,基于提供的知识库内容回答问题。\n\n回答时:\n1. 仅使用知识库内容,不臆造信息\n2. 标注引用来源,使用 [1] [2] 格式\n3. 知识库无相关内容时直接说明',
  },
  {
    name: '代码审查',
    prompt: '你是一名资深代码审查员。基于知识库中的代码规范文档,审查用户提供的代码,指出潜在问题、风格不符或可优化点。\n\n回答格式:\n- 问题描述\n- 具体行号\n- 改进建议\n- 引用规范来源 [n]',
  },
  {
    name: '客户支持',
    prompt: '你是产品客服助手。根据产品文档回答用户使用问题,语气友好耐心。\n\n准则:\n- 给出清晰的步骤说明\n- 必要时附上引用 [n]\n- 解决不了的问题引导用户联系人工',
  },
];

export function PromptTab({ draft, update }: Props) {
  const wordCount = draft.system_prompt.length;

  return (
    <div className="max-w-2xl">
      <Field
        label="System Prompt"
        hint="向模型说明它的身份、能力边界和回答规范"
      >
        <TextArea
          value={draft.system_prompt}
          onChange={(e) => update({ system_prompt: e.target.value })}
          rows={10}
          placeholder="你是一个智能助手…"
        />
        <div className="flex items-center justify-between mt-2">
          <div className="flex flex-wrap gap-2">
            {TEMPLATES.map((t) => (
              <button
                key={t.name}
                onClick={() => update({ system_prompt: t.prompt })}
                className="text-[11px] px-2 py-1 border rounded-md text-gray-600 hover:bg-gray-50"
              >
                {t.name}
              </button>
            ))}
          </div>
          <div className="text-[11px] text-gray-400">{wordCount} 字</div>
        </div>
      </Field>

      <Field label="开场白" hint="用户进入对话时显示的第一条消息">
        <TextInput
          value={draft.opening_message || ''}
          onChange={(e) => update({ opening_message: e.target.value })}
          placeholder="你好,我能帮你解答什么问题?"
        />
      </Field>
    </div>
  );
}
