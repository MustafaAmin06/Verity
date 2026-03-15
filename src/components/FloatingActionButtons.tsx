import { motion } from "framer-motion";
import { getTierCssColor } from "@/lib/mockData";
import type { ConversationBlock } from "@/lib/mockData";

interface FloatingActionButtonsProps {
  blocks: ConversationBlock[];
  onSelect: (blockId: string) => void;
  activeId: string | null;
}

export function FloatingActionButtons({ blocks, onSelect, activeId }: FloatingActionButtonsProps) {
  const assistantBlocks = blocks.filter((b) => b.role === "assistant");

  return (
    <div className="fixed right-3 top-1/2 -translate-y-1/2 z-[9998] flex flex-col gap-2">
      {assistantBlocks.map((block, i) => {
        const topScore = block.sources
          ? Math.max(...block.sources.map((s) => s.score))
          : 0;
        const glowColor = getTierCssColor(topScore);
        const isActive = activeId === block.id;

        return (
          <motion.button
            key={block.id}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: i * 0.05, duration: 0.3, ease: [0.25, 0.1, 0.25, 1] }}
            onClick={() => onSelect(block.id)}
            className="w-11 h-11 rounded-badge flex items-center justify-center font-mono text-sm font-bold transition-all duration-200"
            style={{
              backgroundColor: isActive ? `${glowColor}20` : "rgba(30,30,30,0.9)",
              color: isActive ? glowColor : "rgba(255,255,255,0.7)",
              boxShadow: isActive
                ? `0 0 0 1px ${glowColor}40, 0 0 20px ${glowColor}30`
                : "var(--shadow-fab)",
              backdropFilter: "blur(12px)",
            }}
            whileHover={{
              scale: 1.1,
              backgroundColor: `${glowColor}20`,
              boxShadow: `0 0 0 1px ${glowColor}40, 0 0 20px ${glowColor}30`,
            }}
          >
            {block.sources?.length || 0}
          </motion.button>
        );
      })}
    </div>
  );
}
