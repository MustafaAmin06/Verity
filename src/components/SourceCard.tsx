import { useState } from "react";
import type { Source } from "@/lib/mockData";
import { getTierCssColor } from "@/lib/mockData";
import { ScoreBadge } from "./ScoreBadge";
import { AnalysisPanel } from "./AnalysisPanel";

interface SourceCardProps {
  source: Source;
}

export function SourceCard({ source }: SourceCardProps) {
  const [hovered, setHovered] = useState(false);
  const color = getTierCssColor(source.score);

  return (
    <div
      className="relative rounded-lg transition-all duration-300 ease-standard cursor-pointer group"
      style={{
        backgroundColor: hovered ? `${color}08` : "hsl(var(--sidebar-card))",
        boxShadow: hovered
          ? `0 0 0 1px ${color}25, 0 4px 12px rgba(0,0,0,0.2)`
          : "0 0 0 1px rgba(255,255,255,0.05)",
      }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      {/* Left accent bar */}
      <div
        className="absolute left-0 top-0 bottom-0 w-1 rounded-l-lg transition-opacity duration-300"
        style={{ backgroundColor: color, opacity: hovered ? 1 : 0.5 }}
      />

      <div className="p-3 pl-4">
        <div className="flex items-start gap-3">
          <ScoreBadge score={source.score} />
          <div className="min-w-0 flex-1">
            <p className="text-[11px] text-muted-foreground truncate">{source.domain}</p>
            <a
              href={source.url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-[13px] font-semibold text-foreground leading-tight line-clamp-2 mt-0.5 block transition-colors duration-200 hover:underline"
              style={{ textDecorationColor: color }}
              onMouseEnter={(e) => (e.currentTarget.style.color = color)}
              onMouseLeave={(e) => (e.currentTarget.style.color = "")}
              onClick={(e) => e.stopPropagation()}
            >
              {source.title}
            </a>
            <p className="text-[11px] text-muted-foreground mt-1">{source.date}</p>
          </div>
        </div>

        {/* Expandable analysis */}
        <div
          className="grid transition-[grid-template-rows] duration-300 ease-standard"
          style={{ gridTemplateRows: hovered ? "1fr" : "0fr" }}
        >
          <div className="overflow-hidden">
            <AnalysisPanel source={source} />
          </div>
        </div>
      </div>
    </div>
  );
}
