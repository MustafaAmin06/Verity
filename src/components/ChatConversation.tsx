import { forwardRef } from "react";
import { Link2 } from "lucide-react";
import type { ConversationBlock } from "@/lib/mockData";

interface ChatConversationProps {
  blocks: ConversationBlock[];
  onSourcesClick: (blockId: string) => void;
  refs: Record<string, React.RefObject<HTMLDivElement>>;
}

export const ChatConversation = forwardRef<HTMLDivElement, ChatConversationProps>(
  ({ blocks, onSourcesClick, refs }, _ref) => {
    return (
      <div className="max-w-[760px] mx-auto py-12 px-6 space-y-6">
        {blocks.map((block) => (
          <div
            key={block.id}
            ref={refs[block.id]}
            className={`rounded-lg ${block.role === "user" ? "bg-chat-user p-4 ml-16" : ""}`}
          >
            {block.role === "user" ? (
              <p className="text-foreground text-[15px]">{block.content}</p>
            ) : (
              <div>
                <div
                  className="prose prose-invert prose-sm max-w-none text-[15px] leading-relaxed"
                  dangerouslySetInnerHTML={{ __html: formatMarkdown(block.content) }}
                />
                {block.sources && block.sources.length > 0 && (
                  <div className="mt-4 flex items-center gap-2">
                    {block.sources.slice(0, 2).map((_, i) => (
                      <button
                        key={i}
                        onClick={() => onSourcesClick(block.id)}
                        className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-badge text-[12px] text-muted-foreground transition-colors hover:bg-secondary"
                        style={{ boxShadow: "0 0 0 1px rgba(255,255,255,0.1)" }}
                      >
                        <Link2 className="w-3 h-3" />
                        {i === 1 && block.sources!.length > 2
                          ? `+${block.sources!.length - 1}`
                          : ""}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        ))}
      </div>
    );
  }
);

ChatConversation.displayName = "ChatConversation";

function formatMarkdown(text: string): string {
  return text
    .replace(/## (.+)/g, '<h2 class="text-lg font-semibold text-foreground mt-6 mb-3">$1</h2>')
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\*(.+?)\*/g, "<em>$1</em>")
    .replace(/• /g, '<li class="ml-4 mb-3 list-disc">')
    .replace(/\n\n/g, "</p><p>")
    .replace(/\n/g, "<br/>");
}
