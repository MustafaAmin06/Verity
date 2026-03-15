import { useRef, useState, createRef } from "react";
import { mockConversation } from "@/lib/mockData";
import { FloatingActionButtons } from "@/components/FloatingActionButtons";
import { SourcesSidebar } from "@/components/SourcesSidebar";
import { ChatConversation } from "@/components/ChatConversation";

const Index = () => {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [activeBlockId, setActiveBlockId] = useState<string | null>(null);

  const blockRefs = useRef<Record<string, React.RefObject<HTMLDivElement>>>(
    Object.fromEntries(mockConversation.map((b) => [b.id, createRef<HTMLDivElement>()]))
  );

  const handleSelectBlock = (blockId: string) => {
    const block = mockConversation.find((b) => b.id === blockId);
    if (!block?.sources) return;

    setActiveBlockId(blockId);
    setSidebarOpen(true);

    blockRefs.current[blockId]?.current?.scrollIntoView({
      behavior: "smooth",
      block: "start",
    });
  };

  const activeBlock = mockConversation.find((b) => b.id === activeBlockId);

  return (
    <div className="min-h-screen bg-background">
      {/* Main chat area */}
      <div
        className="transition-all duration-300 ease-standard"
        style={{ marginRight: sidebarOpen ? 340 : 0 }}
      >
        <ChatConversation
          blocks={mockConversation}
          onSourcesClick={handleSelectBlock}
          refs={blockRefs.current}
        />
      </div>

      {/* FABs */}
      <div
        className="transition-all duration-300 ease-standard"
        style={{ marginRight: sidebarOpen ? 340 : 0 }}
      >
        <FloatingActionButtons
          blocks={mockConversation}
          onSelect={handleSelectBlock}
          activeId={activeBlockId}
        />
      </div>

      {/* Sidebar */}
      <SourcesSidebar
        open={sidebarOpen}
        onClose={() => {
          setSidebarOpen(false);
          setActiveBlockId(null);
        }}
        sources={activeBlock?.sources || []}
      />
    </div>
  );
};

export default Index;
