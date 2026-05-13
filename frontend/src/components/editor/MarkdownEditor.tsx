import MDEditor from "@uiw/react-md-editor";

interface MarkdownEditorProps {
  value: string;
  onChange: (next: string) => void;
  height?: number;
  placeholder?: string;
}

export function MarkdownEditor({
  value,
  onChange,
  height = 380,
  placeholder,
}: MarkdownEditorProps) {
  return (
    <div data-color-mode="light" className="rounded-md border border-input">
      <MDEditor
        value={value}
        onChange={(v) => onChange(v ?? "")}
        height={height}
        preview="live"
        textareaProps={placeholder ? { placeholder } : undefined}
      />
    </div>
  );
}
