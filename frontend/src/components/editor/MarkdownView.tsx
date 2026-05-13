import MDEditor from "@uiw/react-md-editor";

interface MarkdownViewProps {
  source: string | null | undefined;
  className?: string;
}

export function MarkdownView({ source, className }: MarkdownViewProps) {
  if (!source) {
    return (
      <div className="text-sm text-muted-foreground italic">（无描述）</div>
    );
  }
  return (
    <div data-color-mode="light" className={className}>
      <MDEditor.Markdown source={source} style={{ background: "transparent" }} />
    </div>
  );
}
