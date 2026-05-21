import { AgentDraft } from './draft';
import { Field, TextInput, TextArea, Segmented } from './FormControls';
import { Visibility } from '@/types';

interface Props {
  draft: AgentDraft;
  update: (patch: Partial<AgentDraft>) => void;
}

export function BasicTab({ draft, update }: Props) {
  return (
    <div className="max-w-xl">
      <Field label="名称">
        <TextInput
          value={draft.name}
          onChange={(e) => update({ name: e.target.value })}
          placeholder="例如:产品手册助手"
        />
      </Field>
      <Field label="描述" hint="简要说明这个 Agent 的用途和能力">
        <TextArea
          value={draft.description || ''}
          onChange={(e) => update({ description: e.target.value })}
          rows={3}
          className="font-sans"
        />
      </Field>
      <Field label="可见性">
        <Segmented<Visibility>
          value={draft.visibility}
          options={[
            { value: 'private', label: '私有' },
            { value: 'workspace', label: '团队' },
            { value: 'public', label: '公开' },
          ]}
          onChange={(v) => update({ visibility: v })}
        />
      </Field>
    </div>
  );
}
