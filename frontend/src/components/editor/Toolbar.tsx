import type { Editor } from "@tiptap/react";
import {
  Bold,
  Code,
  CodeXml,
  Heading1,
  Heading2,
  Heading3,
  Highlighter,
  ImageIcon,
  Italic,
  Link,
  List,
  ListOrdered,
  Quote,
  Redo,
  RemoveFormatting,
  Strikethrough,
  Table,
  Underline,
  Undo,
} from "lucide-react";
import { cn } from "@/lib/utils";

type ToolbarLevel = "full" | "minimal";

interface ToolbarButton {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  action: () => void;
  isActive: boolean;
}

function ToolbarGroup({
  buttons,
  compact,
}: {
  buttons: ToolbarButton[];
  compact?: boolean;
}) {
  return (
    <div className={cn("flex shrink-0 items-center gap-0.5", compact ? "px-0.5" : "px-1")}>
      {buttons.map((btn) => (
        <button
          key={btn.label}
          type="button"
          title={btn.label}
          onClick={btn.action}
          className={cn(
            "rounded p-1.5 text-xs transition-colors",
            btn.isActive
              ? "bg-accent text-accent-foreground"
              : "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
          )}
        >
          <btn.icon className="h-3.5 w-3.5" />
        </button>
      ))}
    </div>
  );
}

function Divider() {
  return <div className="mx-0.5 h-5 w-px shrink-0 bg-border" />;
}

export function Toolbar({
  editor,
  level,
}: {
  editor: Editor;
  level: ToolbarLevel;
}) {
  const boldBtn: ToolbarButton = {
    icon: Bold,
    label: "粗体",
    action: () => editor.chain().focus().toggleBold().run(),
    isActive: editor.isActive("bold"),
  };
  const italicBtn: ToolbarButton = {
    icon: Italic,
    label: "斜体",
    action: () => editor.chain().focus().toggleItalic().run(),
    isActive: editor.isActive("italic"),
  };
  const underlineBtn: ToolbarButton = {
    icon: Underline,
    label: "下划线",
    action: () => editor.chain().focus().toggleUnderline().run(),
    isActive: editor.isActive("underline"),
  };
  const strikeBtn: ToolbarButton = {
    icon: Strikethrough,
    label: "删除线",
    action: () => editor.chain().focus().toggleStrike().run(),
    isActive: editor.isActive("strike"),
  };
  const codeBtn: ToolbarButton = {
    icon: Code,
    label: "行内代码",
    action: () => editor.chain().focus().toggleCode().run(),
    isActive: editor.isActive("code"),
  };
  const bulletBtn: ToolbarButton = {
    icon: List,
    label: "无序列表",
    action: () => editor.chain().focus().toggleBulletList().run(),
    isActive: editor.isActive("bulletList"),
  };
  const orderedBtn: ToolbarButton = {
    icon: ListOrdered,
    label: "有序列表",
    action: () => editor.chain().focus().toggleOrderedList().run(),
    isActive: editor.isActive("orderedList"),
  };
  const blockquoteBtn: ToolbarButton = {
    icon: Quote,
    label: "引用",
    action: () => editor.chain().focus().toggleBlockquote().run(),
    isActive: editor.isActive("blockquote"),
  };
  const codeBlockBtn: ToolbarButton = {
    icon: CodeXml,
    label: "代码块",
    action: () => editor.chain().focus().toggleCodeBlock().run(),
    isActive: editor.isActive("codeBlock"),
  };
  const linkBtn: ToolbarButton = {
    icon: Link,
    label: "链接",
    action: () => {
      const prev = editor.getAttributes("link").href;
      const url = window.prompt("链接地址", prev ?? "https://");
      if (url === null) return;
      if (url === "") {
        editor.chain().focus().unsetLink().run();
      } else {
        editor.chain().focus().setLink({ href: url }).run();
      }
    },
    isActive: editor.isActive("link"),
  };
  const imageBtn: ToolbarButton = {
    icon: ImageIcon,
    label: "图片",
    action: () => {
      const url = window.prompt("图片地址", "https://");
      if (url) {
        editor.chain().focus().setImage({ src: url }).run();
      }
    },
    isActive: false,
  };
  const h1Btn: ToolbarButton = {
    icon: Heading1,
    label: "一级标题",
    action: () => editor.chain().focus().toggleHeading({ level: 1 }).run(),
    isActive: editor.isActive("heading", { level: 1 }),
  };
  const h2Btn: ToolbarButton = {
    icon: Heading2,
    label: "二级标题",
    action: () => editor.chain().focus().toggleHeading({ level: 2 }).run(),
    isActive: editor.isActive("heading", { level: 2 }),
  };
  const h3Btn: ToolbarButton = {
    icon: Heading3,
    label: "三级标题",
    action: () => editor.chain().focus().toggleHeading({ level: 3 }).run(),
    isActive: editor.isActive("heading", { level: 3 }),
  };
  const highlightBtn: ToolbarButton = {
    icon: Highlighter,
    label: "高亮",
    action: () => editor.chain().focus().toggleHighlight().run(),
    isActive: editor.isActive("highlight"),
  };
  const tableBtn: ToolbarButton = {
    icon: Table,
    label: "插入表格",
    action: () =>
      editor
        .chain()
        .focus()
        .insertTable({ rows: 3, cols: 3, withHeaderRow: true })
        .run(),
    isActive: false,
  };
  const undoBtn: ToolbarButton = {
    icon: Undo,
    label: "撤销",
    action: () => editor.chain().focus().undo().run(),
    isActive: false,
  };
  const redoBtn: ToolbarButton = {
    icon: Redo,
    label: "重做",
    action: () => editor.chain().focus().redo().run(),
    isActive: false,
  };
  const clearBtn: ToolbarButton = {
    icon: RemoveFormatting,
    label: "清除格式",
    action: () => editor.chain().focus().unsetAllMarks().clearNodes().run(),
    isActive: false,
  };

  if (level === "minimal") {
    return (
      <div className="flex items-center gap-0 overflow-x-auto rounded-t-md border-b bg-muted/30 px-1.5 py-1">
        <ToolbarGroup buttons={[boldBtn, italicBtn, underlineBtn]} compact />
        <Divider />
        <ToolbarGroup buttons={[bulletBtn, orderedBtn]} compact />
        <Divider />
        <ToolbarGroup buttons={[blockquoteBtn, codeBlockBtn]} compact />
        <Divider />
        <ToolbarGroup buttons={[linkBtn]} compact />
        <Divider />
        <ToolbarGroup
          compact
          buttons={[undoBtn, redoBtn, clearBtn]}
        />
      </div>
    );
  }

  return (
    <div className="flex items-center gap-0 overflow-x-auto rounded-t-md border-b bg-muted/30 px-1.5 py-1">
      <ToolbarGroup buttons={[boldBtn, italicBtn, underlineBtn, strikeBtn, codeBtn]} compact />
      <Divider />
      <ToolbarGroup buttons={[h1Btn, h2Btn, h3Btn]} compact />
      <Divider />
      <ToolbarGroup buttons={[bulletBtn, orderedBtn]} compact />
      <Divider />
      <ToolbarGroup buttons={[blockquoteBtn, codeBlockBtn]} compact />
      <Divider />
      <ToolbarGroup buttons={[linkBtn, imageBtn]} compact />
      <Divider />
      <ToolbarGroup buttons={[highlightBtn]} compact />
      <Divider />
      <ToolbarGroup buttons={[tableBtn]} compact />
      <Divider />
      <ToolbarGroup compact buttons={[undoBtn, redoBtn, clearBtn]} />
    </div>
  );
}
