import { useEffect } from "react";
import { useEditor, EditorContent } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import Placeholder from "@tiptap/extension-placeholder";
import Link from "@tiptap/extension-link";
import Image from "@tiptap/extension-image";
import Highlight from "@tiptap/extension-highlight";
import TextAlign from "@tiptap/extension-text-align";
import Underline from "@tiptap/extension-underline";
import CodeBlockLowlight from "@tiptap/extension-code-block-lowlight";
import { Table } from "@tiptap/extension-table";
import { TableRow } from "@tiptap/extension-table-row";
import { TableCell } from "@tiptap/extension-table-cell";
import { TableHeader } from "@tiptap/extension-table-header";
import { common, createLowlight } from "lowlight";

import { cn } from "@/lib/utils";
import { Toolbar } from "./Toolbar";

const lowlight = createLowlight(common);

type ToolbarLevel = "full" | "minimal" | "none";
type EditorVariant = "default" | "code";

interface RichTextEditorProps {
  value: string;
  onChange: (html: string) => void;
  height?: number;
  placeholder?: string;
  toolbar?: ToolbarLevel;
  readOnly?: boolean;
  variant?: EditorVariant;
  /** 撑满父容器高度(内部滚动),忽略固定 height。父级需是有界高度的 flex 列。 */
  fill?: boolean;
}

export function RichTextEditor({
  value,
  onChange,
  height = 400,
  placeholder,
  toolbar = "full",
  readOnly = false,
  variant = "default",
  fill = false,
}: RichTextEditorProps) {
  const editor = useEditor({
    extensions: [
      StarterKit.configure({
        codeBlock: false,
      }),
      Placeholder.configure({ placeholder }),
      Link.configure({ openOnClick: false }),
      // allowBase64：允许内嵌 data-URI 图（否则 TipTap 解析时会把 base64 图整块剥掉，
      // 知识库里内嵌的图/图表就会消失，只剩图注文字）。
      Image.configure({ inline: false, allowBase64: true }),
      Highlight,
      TextAlign.configure({ types: ["heading", "paragraph"] }),
      Underline,
      CodeBlockLowlight.configure({ lowlight }),
      Table.configure({ resizable: true }),
      TableRow,
      TableCell,
      TableHeader,
    ],
    content: value,
    editable: !readOnly,
    onUpdate: ({ editor }) => {
      onChange(editor.getHTML());
    },
  });

  useEffect(() => {
    if (!editor) return;
    if (editor.getHTML() === value) return;
    editor.commands.setContent(value, { emitUpdate: false });
  }, [editor, value]);

  if (!editor) return null;

  const isCodeVariant = variant === "code";

  return (
    <div
      className={cn(
        isCodeVariant
          ? "bg-transparent"
          : "rounded-md border border-input bg-background",
        readOnly ? (isCodeVariant ? "" : "p-4") : "flex flex-col",
        fill && "min-h-0 flex-1",
      )}
    >
      {toolbar !== "none" && !readOnly && !isCodeVariant && (
        <Toolbar editor={editor} level={toolbar} />
      )}
      <EditorContent
        editor={editor}
        spellCheck={!isCodeVariant}
        className={cn(
          "rich-editor-content prose prose-sm max-w-none",
          isCodeVariant && "rich-editor-code-only",
          !readOnly && !isCodeVariant && "flex-1 overflow-y-auto border-t px-4 py-3",
          !readOnly && isCodeVariant && "overflow-y-auto",
          fill && "min-h-0 flex-1 rich-editor-fill",
        )}
        style={readOnly ? {} : fill ? {} : { minHeight: height, maxHeight: height }}
      />
    </div>
  );
}
