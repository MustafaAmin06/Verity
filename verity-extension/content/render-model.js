window.Verity = window.Verity || {};

function primaryScore(source) {
  if (!source) return null;
  if (source.overall_score !== undefined && source.overall_score !== null) {
    return source.overall_score;
  }
  if (source.composite_score !== undefined && source.composite_score !== null) {
    return source.composite_score;
  }
  return null;
}

function scoreColor(score100) {
  if (score100 === undefined || score100 === null) return "#9ca3af";
  if (score100 >= 75) return "#4ade80";
  if (score100 >= 50) return "#facc15";
  if (score100 >= 25) return "#f87171";
  return "#9ca3af";
}

function themeColor(source, fallbackScore) {
  const map = {
    green: "#4ade80",
    amber: "#facc15",
    red: "#f87171",
    gray: "#9ca3af",
  };
  if (source && typeof source.color === "string" && source.color.trim()) {
    return map[source.color] || source.color;
  }
  return scoreColor(fallbackScore);
}

function verdictLabel(source) {
  if (source?.verdict_label) return source.verdict_label;
  const map = {
    supported: "Supported by source",
    cautious_support: "Some support, but use caution",
    relevant_unverified: "Relevant, but not verified",
    contradicted: "Contradicted by source",
    inaccessible: "Inaccessible or insufficient evidence",
    reliable: "Reliable",
    unverified: "Unverified",
  };
  if (source?.verdict && map[source.verdict]) return map[source.verdict];
  if (source?.live === false) return "Inaccessible or insufficient evidence";
  return "Unverified";
}

function titleCase(text) {
  return String(text || "")
    .replace(/[_-]+/g, " ")
    .split(/\s+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function scoreDescriptor(score) {
  if (score === undefined || score === null) return "Not scored";
  if (score >= 80) return "High";
  if (score >= 60) return "Moderate";
  if (score >= 40) return "Limited";
  return "Weak";
}

function humanizeSupportClass(value) {
  const map = {
    direct_support: "Direct support",
    qualified_support: "Qualified support",
    topic_relevant_unverified: "Relevant, not verified",
    mixed_or_ambiguous: "Mixed or ambiguous",
    contradicted: "Contradicted",
  };
  return map[value] || titleCase(value || "unknown");
}

function humanizeEvidenceSpecificity(value) {
  const map = {
    direct: "Direct evidence",
    paraphrased: "Paraphrased evidence",
    weak: "Weak evidence",
    none: "No clear evidence",
  };
  return map[value] || titleCase(value || "unknown");
}

function hasRubricSignals(signals) {
  if (!signals || typeof signals !== "object") return false;
  const required = [
    "source_credibility_score",
    "claim_support_score",
    "decision_confidence_score",
  ];
  return required.every((key) => Number.isFinite(signals[key]));
}

function humanizeTier(tier) {
  const map = {
    academic_journal: "Academic / Research",
    official_body: "Official / Government",
    medical_authority: "Medical Authority",
    established_news: "Established News",
    independent_blog: "Independent / Blog",
    reference_tertiary: "Reference / Tertiary",
    flagged: "Flagged Source",
  };
  return map[tier] || "Unknown";
}

function authorDisplay(source) {
  const signals = source.signals || {};
  if (source.author) {
    const hIndex = signals.oa_author_h_index;
    return hIndex ? `${source.author} · h-index ${hIndex}` : source.author;
  }
  if (source.author_label) return source.author_label;
  if (
    source.authorship_type === "institutional" ||
    ["academic_journal", "official_body", "medical_authority"].includes(
      signals.domain_tier
    )
  ) {
    return "Institutional page";
  }
  return "Unknown";
}

function humanizeAuthoritySource(source) {
  const map = {
    registry: "Curated registry",
    openalex: "OpenAlex",
    crossref: "Crossref",
    ror: "ROR",
    wikidata: "Wikidata",
    learned_domain: "Learned domain cache",
  };
  return map[source] || source || "Unknown";
}

function buildFactsSection(label, items) {
  const filtered = items.filter((item) => item && item.value);
  if (filtered.length === 0) return null;
  return {
    type: "facts",
    label,
    items: filtered,
  };
}

function verdictTone(source, score) {
  if (source?.verdict === "supported" || source?.verdict === "reliable") {
    return "positive";
  }
  if (
    source?.verdict === "cautious_support" ||
    source?.verdict === "relevant_unverified"
  ) {
    return "caution";
  }
  if (source?.verdict === "contradicted" || source?.live === false) {
    return "negative";
  }
  if (score != null && score >= 75) return "positive";
  if (score != null && score >= 45) return "caution";
  if (score != null && score < 25) return "negative";
  return "neutral";
}

function buildParagraphSection(label, paragraphs) {
  const filtered = (paragraphs || []).filter((item) => item && item.text);
  if (filtered.length === 0) return null;
  return {
    type: "paragraphs",
    label,
    paragraphs: filtered,
  };
}

function buildFlagsSection(signals) {
  const items = [];
  if (signals.retrieval_limited) items.push("Limited source access");
  if (signals.metadata_only) items.push("Metadata only");
  if (items.length === 0) return null;
  return {
    type: "flags",
    items,
  };
}

function buildRubricSection(signals) {
  if (!hasRubricSignals(signals)) return null;
  return {
    type: "rubric",
    label: "Rubric breakdown",
    axes: [
      {
        label: "Credibility",
        value: signals.source_credibility_score,
        descriptor: scoreDescriptor(signals.source_credibility_score),
      },
      {
        label: "Claim support",
        value: signals.claim_support_score,
        descriptor: scoreDescriptor(signals.claim_support_score),
      },
      {
        label: "Confidence",
        value: signals.decision_confidence_score,
        descriptor: scoreDescriptor(signals.decision_confidence_score),
      },
    ],
  };
}

function buildVerificationSection(signals) {
  return buildFactsSection("Verification details", [
    {
      label: "Support class",
      value: humanizeSupportClass(signals.support_class),
    },
    {
      label: "Evidence specificity",
      value: humanizeEvidenceSpecificity(signals.evidence_specificity),
    },
    {
      label: "Confidence level",
      value: titleCase(signals.decision_confidence_level || "unknown"),
    },
    {
      label: "Contradiction strength",
      value:
        signals.contradiction_strength &&
        signals.contradiction_strength !== "none"
          ? titleCase(signals.contradiction_strength)
          : "",
    },
    {
      label: "Matched terms",
      value:
        Array.isArray(signals.matched_terms) && signals.matched_terms.length > 0
          ? signals.matched_terms.join(", ")
          : "",
    },
  ]);
}

function buildMetadataSection(source, signals) {
  const authorityLabel =
    signals.authority_label ||
    source.authority_name ||
    humanizeTier(signals.domain_tier);
  const authoritySource = humanizeAuthoritySource(
    source.authority_source || signals.authority_source
  );
  const authorityConfidence =
    source.authority_confidence || signals.authority_confidence;

  return buildFactsSection("Source metadata", [
    {
      label: "Source status",
      value: source.live !== false ? "Live" : "Unavailable",
      tone: source.live !== false ? "green" : "red",
    },
    {
      label: "Source tier",
      value: humanizeTier(signals.domain_tier),
    },
    {
      label: "Author",
      value: authorDisplay(source),
    },
    {
      label: "Publication",
      value: source.domain || "",
    },
    {
      label: "Authority",
      value: authorityLabel,
    },
    {
      label: "Verified via",
      value: authorityConfidence
        ? `${authoritySource} · ${authorityConfidence}`
        : authoritySource,
    },
    {
      label: "Publication date",
      value: source.date || "",
    },
    {
      label: "Publisher",
      value: source.publisher || "",
    },
    {
      label: "Citations",
      value:
        signals.oa_cited_by_count != null && signals.oa_cited_by_count > 0
          ? signals.oa_cited_by_count.toLocaleString()
          : "",
    },
  ]);
}

function buildLegacySection(source, signals, score) {
  const scoreFive =
    score === undefined || score === null ? "" : `${(score / 20).toFixed(1)} / 5.0`;
  const authorityLabel =
    signals.authority_label ||
    source.authority_name ||
    humanizeTier(signals.domain_tier);
  const authoritySource = humanizeAuthoritySource(
    source.authority_source || signals.authority_source
  );
  const authorityConfidence =
    source.authority_confidence || signals.authority_confidence;
  const relevanceScore = signals.relevance_score;

  return buildFactsSection("Source details", [
    {
      label: "Score",
      value: scoreFive || "—",
    },
    {
      label: "Source status",
      value: source.live !== false ? "Live" : "Unavailable",
      tone: source.live !== false ? "green" : "red",
    },
    {
      label: "Source tier",
      value: humanizeTier(signals.domain_tier),
    },
    {
      label: "Relevance",
      value:
        relevanceScore != null ? `${relevanceScore}% overlap` : "Not assessed",
    },
    {
      label: "Author",
      value: authorDisplay(source),
    },
    {
      label: "Publication",
      value: source.domain || "",
    },
    {
      label: "Authority",
      value: authorityLabel,
    },
    {
      label: "Verified via",
      value: authorityConfidence
        ? `${authoritySource} · ${authorityConfidence}`
        : authoritySource,
    },
    {
      label: "Publisher",
      value: source.publisher || "",
    },
    {
      label: "Citations",
      value:
        signals.oa_cited_by_count != null && signals.oa_cited_by_count > 0
          ? signals.oa_cited_by_count.toLocaleString()
          : "",
    },
  ]);
}

function buildTagsSection(source) {
  if (!Array.isArray(source.topics) || source.topics.length === 0) return null;
  return {
    type: "tags",
    items: source.topics.slice(0, 4),
  };
}

function buildFundersSection(source) {
  if (!Array.isArray(source.funders) || source.funders.length === 0) return null;
  return {
    type: "note",
    text: `Funded by: ${source.funders.join(", ")}`,
  };
}

function buildContextSection(source) {
  const contextText = typeof source.context === "string" ? source.context.trim() : "";
  if (!contextText) return null;
  return {
    type: "context",
    label: "Exact context picked up",
    text: contextText,
  };
}

function normalizeSource(source, index) {
  const score = primaryScore(source);
  const signals = source.signals || {};
  const summaryText = source.reason || source.description || source.implication || "";
  const verificationSection = buildVerificationSection(signals);
  const metadataSection = buildMetadataSection(source, signals);
  const legacySection = buildLegacySection(source, signals, score);
  const contextSection = buildContextSection(source);
  const topicsSection = buildTagsSection(source);
  const fundersSection = buildFundersSection(source);
  const rubricSection = buildRubricSection(signals);

  const normalized = {
    id: source.url || `source:${index}`,
    url: source.url || "",
    title: source.title || source.label || source.url || "",
    sourceMeta: [source.domain, source.date].filter(Boolean).join(" • "),
    verdictLabel: verdictLabel(source),
    verdictTone: verdictTone(source, score),
    score,
    scoreDisplay: score !== undefined && score !== null ? String(score) : "—",
    scoreSuffix: score !== undefined && score !== null ? "/100" : "",
    summaryPreview: summaryText,
    analysisSummary: source.reason || source.description || "",
    analysisImplication: source.implication || "",
    rubricAxes: rubricSection ? rubricSection.axes.map((axis) => ({
      ...axis,
      scoreText: axis.value != null ? `${axis.value}/100` : "—",
    })) : [],
    verificationFacts: verificationSection ? verificationSection.items : legacySection ? legacySection.items : [],
    metadataFacts: metadataSection ? metadataSection.items : [],
    flags: buildFlagsSection(signals)?.items || [],
    topics: topicsSection ? topicsSection.items : [],
    fundersLabel: fundersSection ? fundersSection.text : "",
    contextText: contextSection ? contextSection.text : "",
  };

  if (!normalized.analysisSummary) {
    normalized.analysisSummary = summaryText;
  }

  if (!normalized.verificationFacts.length && legacySection) {
    normalized.verificationFacts = legacySection.items;
  }

  return normalized;
}

window.Verity.renderModel = {
  normalizeResult(data) {
    const rawSources = data.sources || data.scraped_sources || [];
    return rawSources
      .map((source, index) => normalizeSource(source, index))
      .sort((left, right) => (right.score || 0) - (left.score || 0));
  },

  normalizeSource,
};
