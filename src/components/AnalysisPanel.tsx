import type { Source } from "@/lib/mockData";
import { getTierCssColor } from "@/lib/mockData";

interface AnalysisPanelProps {
  source: Source;
}

export function AnalysisPanel({ source }: AnalysisPanelProps) {
  const color = getTierCssColor(source.score);

  return (
    <div className="overflow-hidden">
      <div className="pt-2 pb-1 space-y-3">
        {/* Thin divider */}
        <div className="h-px w-full" style={{ backgroundColor: "rgba(255,255,255,0.08)" }} />

        {/* 2×2 Metadata grid */}
        <div className="grid grid-cols-2 gap-x-4 gap-y-2">
          <div>
            <span className="text-[10px] uppercase tracking-wider text-muted-foreground">Score</span>
            <p className="text-sm font-bold tabular-nums" style={{ color }}>
              {source.score.toFixed(1)} / 5.0
            </p>
          </div>
          <div>
            <span className="text-[10px] uppercase tracking-wider text-muted-foreground">URL Status</span>
            <p
              className="text-sm font-medium"
              style={{ color: source.urlStatus === "resolves" ? "#22c55e" : "#ef4444" }}
            >
              {source.urlStatus === "resolves" ? "Live" : "Down"}
            </p>
          </div>
          <div>
            <span className="text-[10px] uppercase tracking-wider text-muted-foreground">Source Tier</span>
            <p className="text-sm text-foreground">{source.category}</p>
          </div>
          <div>
            <span className="text-[10px] uppercase tracking-wider text-muted-foreground">Relevance</span>
            <p className="text-sm text-foreground">{source.relevance}% overlap</p>
          </div>
        </div>

        {/* Author & Publication row */}
        <div className="flex items-baseline gap-1.5 text-[10px] flex-wrap">
          <span className="uppercase tracking-wider text-muted-foreground">Author</span>
          <span className="text-sm text-foreground font-medium">{source.author}</span>
          <span className="uppercase tracking-wider text-muted-foreground">Publication</span>
          <span className="text-sm text-foreground font-medium">{source.publication}</span>
        </div>

        {/* Analysis summary */}
        <p className="text-xs leading-[1.6]" style={{ color: "#d1d5db" }}>
          {source.summary}
        </p>
      </div>
    </div>
  );
}
