import { motion, AnimatePresence } from "framer-motion";
import { X } from "lucide-react";
import type { Source } from "@/lib/mockData";
import { getAverageScore, getTierCssColor } from "@/lib/mockData";
import { SourceCard } from "./SourceCard";
import { ScoreBadge } from "./ScoreBadge";

interface SourcesSidebarProps {
  open: boolean;
  onClose: () => void;
  sources: Source[];
}

export function SourcesSidebar({ open, onClose, sources }: SourcesSidebarProps) {
  const avg = getAverageScore(sources);
  const topSource = [...sources].sort((a, b) => b.score - a.score)[0];
  const reliable = sources.filter((s) => s.score >= 3.5).length;
  const caution = sources.filter((s) => s.score >= 2.5 && s.score < 3.5).length;
  const unreliable = sources.filter((s) => s.score < 2.5).length;

  return (
    <AnimatePresence>
      {open && (
        <motion.aside
          initial={{ x: 340 }}
          animate={{ x: 0 }}
          exit={{ x: 340 }}
          transition={{ duration: 0.25, ease: [0.25, 0.1, 0.25, 1] }}
          className="fixed right-0 top-0 h-screen w-[340px] z-[9999] flex flex-col"
          style={{
            backgroundColor: "hsl(var(--sidebar-bg))",
            borderLeft: "1px solid rgba(255,255,255,0.05)",
            boxShadow: "var(--shadow-layered)",
          }}
        >
          {/* Header */}
          <div
            className="shrink-0 p-3"
            style={{
              backgroundColor: "hsl(var(--sidebar-header))",
              borderBottom: "1px solid rgba(255,255,255,0.05)",
            }}
          >
            <div className="flex items-center justify-between mb-1">
              <h2 className="text-sm font-bold text-foreground">Cited Sources</h2>
              <button
                onClick={onClose}
                className="w-7 h-7 flex items-center justify-center rounded-md hover:bg-secondary transition-colors"
              >
                <X className="w-4 h-4 text-muted-foreground" />
              </button>
            </div>
            <div className="flex items-center justify-between">
              <p className="text-[11px] tabular-nums text-muted-foreground">
                {sources.length} sources · Average score {avg.toFixed(1)} / 5.0
              </p>
              <div className="flex items-center gap-3">
                {reliable > 0 && (
                  <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
                    <span className="w-2 h-2 rounded-badge bg-tier-5" /> Reliable
                  </span>
                )}
                {caution > 0 && (
                  <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
                    <span className="w-2 h-2 rounded-badge bg-tier-3" /> Caution
                  </span>
                )}
                {unreliable > 0 && (
                  <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
                    <span className="w-2 h-2 rounded-badge bg-tier-1" /> Unreliable
                  </span>
                )}
              </div>
            </div>
          </div>

          {/* Source list */}
          <div className="flex-1 overflow-y-auto p-3 space-y-2">
            {/* Primary sources */}
            {sources.slice(0, 3).map((source) => (
              <SourceCard key={source.id} source={source} />
            ))}

            {sources.length > 3 && (
              <p className="text-[12px] font-medium text-muted-foreground pt-2 pb-1">More</p>
            )}

            {sources.slice(3).map((source) => (
              <SourceCard key={source.id} source={source} />
            ))}
          </div>

          {/* Footer - Top source */}
          {topSource && (
            <div
              className="shrink-0 p-4"
              style={{
                backgroundColor: "hsl(var(--sidebar-footer))",
                borderTop: "1px solid rgba(255,255,255,0.1)",
              }}
            >
              <div className="flex items-center gap-3">
                <ScoreBadge score={topSource.score} size="lg" />
                <div className="min-w-0 flex-1">
                  <h4 className="text-[12px] font-semibold text-foreground truncate">{topSource.title}</h4>
                  <p className="text-[10px] text-muted-foreground truncate">{topSource.url}</p>
                </div>
                <span
                  className="text-[10px] px-2 py-0.5 rounded-md shrink-0"
                  style={{
                    backgroundColor: "rgba(34,197,94,0.1)",
                    color: "#22c55e",
                    border: "1px solid rgba(34,197,94,0.2)",
                  }}
                >
                  {topSource.category}
                </span>
                <span className="flex items-center gap-1 text-[10px] shrink-0" style={{ color: "#22c55e" }}>
                  <span className="w-1.5 h-1.5 rounded-badge bg-tier-5 animate-pulse" />
                  Live
                </span>
              </div>
            </div>
          )}
        </motion.aside>
      )}
    </AnimatePresence>
  );
}
