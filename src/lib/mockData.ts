export interface Source {
  id: string;
  score: number;
  domain: string;
  title: string;
  url: string;
  date: string;
  category: string;
  relevance: number;
  urlStatus: "resolves" | "broken";
  summary: string;
  author: string;
  publication: string;
}

export interface ConversationBlock {
  id: string;
  role: "user" | "assistant";
  content: string;
  sources?: Source[];
}

export const mockSources: Source[] = [
  {
    id: "s1",
    score: 4.7,
    domain: "mdpi.com",
    title: "Coffee consumption and longevity: a 2025 review",
    url: "https://www.mdpi.com/2072-6643/nutrients-review-2025",
    date: "January 9, 2025",
    category: "Academic / Research",
    relevance: 96,
    urlStatus: "resolves",
    summary: "Comprehensive meta-analysis published in Nutrients examining longitudinal studies on coffee intake and mortality outcomes across 1.2M participants.",
    author: "Dr. James H. Freeman",
    publication: "Nutrients Journal",
  },
  {
    id: "s2",
    score: 4.5,
    domain: "pubmed.ncbi.nlm.nih.gov",
    title: "Telomere length and moderate coffee intake",
    url: "https://pubmed.ncbi.nlm.nih.gov/example",
    date: "September 4, 2024",
    category: "Academic / Research",
    relevance: 88,
    urlStatus: "resolves",
    summary: "NIH-funded study linking moderate caffeine consumption to telomere preservation in adults aged 45-65. Sample size: 4,200 participants over 8 years.",
    author: "Dr. Maria Chen",
    publication: "PubMed Central",
  },
  {
    id: "s3",
    score: 4.3,
    domain: "diabetesjournals.org",
    title: "Coffee and Type 2 Diabetes risk: a meta-analysis",
    url: "https://diabetesjournals.org/example",
    date: "June 17, 2024",
    category: "Academic / Research",
    relevance: 91,
    urlStatus: "resolves",
    summary: "Meta-analysis of 30 prospective studies showing 25-30% reduced T2D risk with 3-5 cups daily intake, independent of caffeine content.",
    author: "Dr. Ankit Patel",
    publication: "Diabetes Care",
  },
  {
    id: "s4",
    score: 4.6,
    domain: "academic.oup.com",
    title: "The DECAF study: coffee and cardiac arrhythmia",
    url: "https://academic.oup.com/example",
    date: "February 11, 2025",
    category: "Academic / Research",
    relevance: 82,
    urlStatus: "resolves",
    summary: "Randomized clinical trial debunking the link between coffee and atrial fibrillation, showing potential protective effects on cardiac rhythm.",
    author: "Prof. Elena Vasquez",
    publication: "European Heart Journal",
  },
  {
    id: "s5",
    score: 4.4,
    domain: "hsph.harvard.edu",
    title: "Harvard study on coffee and depression risk",
    url: "https://hsph.harvard.edu/example",
    date: "November 29, 2023",
    category: "Academic / Research",
    relevance: 78,
    urlStatus: "resolves",
    summary: "Harvard School of Public Health longitudinal study finding significant inverse relationship between moderate coffee consumption and clinical depression diagnosis.",
    author: "Dr. Sarah Lindström",
    publication: "HSPH Research Quarterly",
  },
  {
    id: "s6",
    score: 1.8,
    domain: "alzheimers-research-blog.xyz",
    title: "Caffeine may prevent Alzheimer's - unverified claims",
    url: "https://alzheimers-research-blog.xyz/example",
    date: "March 2, 2024",
    category: "Blog / Opinion",
    relevance: 45,
    urlStatus: "broken",
    summary: "Unvetted blog post making unsupported claims about caffeine preventing Alzheimer's disease. No peer review, citations are circular or broken.",
    author: "Unknown",
    publication: "Alzheimer's Research Blog",
  },
];

export const mockConversation: ConversationBlock[] = [
  {
    id: "msg-1",
    role: "user",
    content: "What does current research say about the health benefits of coffee?",
  },
  {
    id: "msg-2",
    role: "assistant",
    content: `Scientific consensus has shifted significantly over the last decade. Current research from major health institutions — including **Harvard Health**, **Johns Hopkins**, and the **Mayo Clinic** — suggests that for most adults, moderate coffee consumption is not only safe but likely beneficial.

## Core Health Benefits

• **Longevity:** Large-scale studies, including a 2025 review in *Nutrients*, indicate that moderate coffee intake (3-5 cups daily) is associated with reduced overall mortality. One study suggested it may even add up to **two extra years** of "healthy aging" by slowing biological aging at the cellular level (protecting telomeres).

• **Metabolic Health:** Consistent evidence shows a **25-30% lower risk of Type 2 Diabetes** for regular drinkers. This benefit exists for both caffeinated and decaffeinated coffee, suggesting it's the polyphenols (antioxidants), not just the caffeine, doing the work.

• **Cardiovascular Support:** Recent research (2025) published in the *European Heart Journal* found that coffee drinkers have a lower risk of heart failure and stroke. Interestingly, a clinical trial (DECAF study) recently debunked the myth that coffee triggers AFib, showing it may actually **reduce** irregular heartbeat recurrence by up to 40% in some patients.

• **Neuroprotection:** Caffeine is linked to a significantly lower risk of **Parkinson's** and **Alzheimer's** diseases. It also acts as a mild antidepressant; studies from Harvard show a decreased risk of depression and suicide among moderate drinkers.`,
    sources: mockSources,
  },
];

export function getTierColor(score: number): string {
  if (score >= 4.5) return "tier-5";
  if (score >= 3.5) return "tier-4";
  if (score >= 2.5) return "tier-3";
  if (score >= 1.5) return "tier-2";
  return "tier-1";
}

export function getTierHsl(score: number): string {
  if (score >= 4.5) return "var(--tier-5)";
  if (score >= 3.5) return "var(--tier-4)";
  if (score >= 2.5) return "var(--tier-3)";
  if (score >= 1.5) return "var(--tier-2)";
  return "var(--tier-1)";
}

export function getTierCssColor(score: number): string {
  if (score >= 4.5) return "#22c55e";
  if (score >= 3.5) return "#84cc16";
  if (score >= 2.5) return "#f59e0b";
  if (score >= 1.5) return "#f97316";
  return "#ef4444";
}

export function getAverageScore(sources: Source[]): number {
  return Math.round((sources.reduce((a, s) => a + s.score, 0) / sources.length) * 10) / 10;
}
