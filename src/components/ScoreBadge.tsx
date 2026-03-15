import { getTierCssColor } from "@/lib/mockData";

interface ScoreBadgeProps {
  score: number;
  size?: "sm" | "md" | "lg";
}

const sizes = {
  sm: "w-7 h-7 text-[11px]",
  md: "w-8 h-8 text-[12px]",
  lg: "w-10 h-10 text-[14px]",
};

export function ScoreBadge({ score, size = "md" }: ScoreBadgeProps) {
  const color = getTierCssColor(score);

  return (
    <span
      className={`${sizes[size]} flex items-center justify-center rounded-badge font-mono font-bold tabular-nums shrink-0`}
      style={{
        backgroundColor: `${color}20`,
        color: color,
        boxShadow: `0 0 0 1px ${color}30`,
      }}
    >
      {score.toFixed(1)}
    </span>
  );
}
