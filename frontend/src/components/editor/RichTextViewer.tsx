import { RichTextEditor } from "./RichTextEditor";

interface RichTextViewerProps {
  source: string | null | undefined;
  className?: string;
}

export function RichTextViewer({
  source,
  className,
}: RichTextViewerProps) {
  if (!source) {
    return (
      <div className="text-sm text-muted-foreground italic">（无描述）</div>
    );
  }

  const isHtml = /<[a-z][\s\S]*>/i.test(source);

  return (
    <div className={className}>
      <RichTextEditor
        value={isHtml ? source : `<p>${source.replace(/\n/g, "<br>")}</p>`}
        onChange={() => {}}
        toolbar="none"
        readOnly
        height={0}
      />
    </div>
  );
}
