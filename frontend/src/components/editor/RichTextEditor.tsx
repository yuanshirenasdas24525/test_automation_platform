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

interface RichTextEditorProps {
  value: string;
  onChange: (html: string) => void;
  height?: number;
  placeholder?: string;
  toolbar?: ToolbarLevel;
  readOnly?: boolean;
}

export function RichTextEditor({
  value,
  onChange,
  height = 400,
  placeholder,
  toolbar = "full",
  readOnly = false,
}: RichTextEditorProps) {
  const editor = useEditor({
    extensions: [
      StarterKit.configure({
        codeBlock: false,
      }),
      Placeholder.configure({ placeholder }),
      Link.configure({ openOnClick: false }),
      Image.configure({ inline: false }),
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

  if (!editor) return null;

  return (
    <div
      className={cn(
        "rounded-md border border-input bg-background",
        readOnly ? "p-4" : "flex flex-col",
      )}
    >
      {toolbar !== "none" && !readOnly && (
        <Toolbar editor={editor} level={toolbar} />
      )}
      <EditorContent
        editor={editor}
        className={cn(
          "rich-editor-content prose prose-sm max-w-none",
          !readOnly && "flex-1 overflow-y-auto border-t px-4 py-3",
        )}
        style={readOnly ? {} : { minHeight: height, maxHeight: height }}
      />
    </div>
  );
}
