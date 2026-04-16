import React, { useEffect, useMemo, useState } from "react";

function Glyph({ name }) {
  return <span className={`verity-react-glyph verity-react-glyph--${name}`} aria-hidden="true" />;
}

function formatSourceCount(count) {
  if (!count) return "Source analysis";
  return `${count} source${count === 1 ? "" : "s"} detected`;
}

function IdleCard({ sourceCount, onCheck }) {
  return (
    <button type="button" className="verity-react-cta" onClick={onCheck}>
      <span className="verity-react-cta__glow" />
      <span className="verity-react-cta__inner">
        <span className="verity-react-cta__eyebrow">
          <Glyph name="spark" />
          Verity inline analysis
        </span>
        <span className="verity-react-cta__title">Check sources with Verity</span>
        <span className="verity-react-cta__meta">{formatSourceCount(sourceCount)}</span>
      </span>
      <span className="verity-react-cta__action">Open</span>
    </button>
  );
}

function LoadingCard({ progressText, sourceCount }) {
  return (
    <div className="verity-react-panel verity-react-panel--loading">
      <div className="verity-react-panel__chrome" />
      <div className="verity-react-loading">
        <div className="verity-react-loading__eyebrow">
          <Glyph name="pulse" />
          Running source analysis
        </div>
        <div className="verity-react-loading__title">{progressText || formatSourceCount(sourceCount)}</div>
        <div className="verity-react-loading__tracks" aria-hidden="true">
          <span className="verity-react-loading__bar" />
          <span className="verity-react-loading__bar" />
          <span className="verity-react-loading__bar" />
        </div>
      </div>
    </div>
  );
}

function ErrorCard({ message, actionLabel, onAction }) {
  return (
    <div className="verity-react-panel verity-react-panel--error">
      <div className="verity-react-panel__chrome" />
      <div className="verity-react-error">
        <div className="verity-react-error__eyebrow">
          <Glyph name="alert" />
          Verity couldn&apos;t complete the check
        </div>
        <div className="verity-react-error__message">{message}</div>
        <button type="button" className="verity-react-secondary-action" onClick={onAction}>
          {actionLabel}
        </button>
      </div>
    </div>
  );
}

function FactGrid({ title, items, dense }) {
  if (!items?.length) return null;

  return (
    <section className="verity-react-section">
      <div className="verity-react-section__label">
        <Glyph name="section" />
        {title}
      </div>
      <div className={`verity-react-fact-grid${dense ? " verity-react-fact-grid--dense" : ""}`}>
        {items.map((item) => (
          <div key={`${title}-${item.label}`} className="verity-react-fact-card">
            <div className="verity-react-fact-card__label">{item.label}</div>
            <div
              className={`verity-react-fact-card__value${
                item.tone ? ` verity-react-fact-card__value--${item.tone}` : ""
              }`}
            >
              {item.value}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function RubricSection({ axes }) {
  if (!axes?.length) return null;

  return (
    <section className="verity-react-section">
      <div className="verity-react-section__label">
        <Glyph name="section" />
        Rubric breakdown
      </div>
      <div className="verity-react-rubric">
        {axes.map((axis) => (
          <div key={axis.label} className="verity-react-rubric__row">
            <div className="verity-react-rubric__head">
              <div>
                <div className="verity-react-rubric__label">{axis.label}</div>
                <div className="verity-react-rubric__descriptor">{axis.descriptor}</div>
              </div>
              <div className="verity-react-rubric__score">{axis.scoreText}</div>
            </div>
            <div className="verity-react-rubric__track">
              <div
                className="verity-react-rubric__fill"
                style={{ width: `${Math.max(0, Math.min(100, axis.value || 0))}%` }}
              />
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function ContextDisclosure({ text, isOpen, onToggle }) {
  if (!text) return null;

  return (
    <section className="verity-react-section verity-react-context" onClick={(event) => event.stopPropagation()}>
      <button
        type="button"
        className="verity-react-secondary-action"
        aria-expanded={isOpen}
        onClick={onToggle}
      >
        {isOpen ? "Hide exact context" : "Show exact context"}
      </button>
      <div className={`verity-react-context__panel${isOpen ? " is-open" : ""}`}>
        <div className="verity-react-context__panel-inner">
          <div className="verity-react-context__label">Exact context picked up</div>
          <pre className="verity-react-context__text">{text}</pre>
        </div>
      </div>
    </section>
  );
}

function SourceCard({
  source,
  isExpanded,
  isContextOpen,
  onToggleExpand,
  onToggleContext,
}) {
  const handleLinkClick = (event) => {
    event.stopPropagation();
  };

  return (
    <article
      className={`verity-react-source-card verity-react-source-card--${source.verdictTone}${
        isExpanded ? " is-expanded" : ""
      }`}
      onClick={onToggleExpand}
      data-expanded={isExpanded ? "true" : "false"}
    >
      <div className="verity-react-source-card__chrome" />

      <div className="verity-react-source-card__hero">
        <div className="verity-react-source-card__hero-main">
          <div className="verity-react-source-card__hero-topline">
            <span className="verity-react-source-card__badge">{source.verdictLabel}</span>
          </div>
          <a
            href={source.url || undefined}
            target="_blank"
            rel="noopener noreferrer"
            className="verity-react-source-card__title"
            onClick={handleLinkClick}
          >
            {source.title}
          </a>
          <div className="verity-react-source-card__meta">{source.sourceMeta}</div>
          {source.summaryPreview ? (
            <p className="verity-react-source-card__summary-preview">{source.summaryPreview}</p>
          ) : null}
        </div>

        <div className="verity-react-source-card__score">
          <div className="verity-react-source-card__score-label">Verity score</div>
          <div className="verity-react-source-card__score-value">
            {source.scoreDisplay}
            <span>{source.scoreSuffix}</span>
          </div>
        </div>
      </div>

      <div className="verity-react-source-card__details-shell">
        <div className="verity-react-source-card__details">
          <section className="verity-react-section">
            <div className="verity-react-section__label">
              <Glyph name="section" />
              Decision summary
            </div>
            <div className="verity-react-prose">
              {source.analysisSummary ? <p>{source.analysisSummary}</p> : null}
              {source.analysisImplication ? (
                <p className="verity-react-prose__muted">{source.analysisImplication}</p>
              ) : null}
            </div>
            {source.flags?.length ? (
              <div className="verity-react-flags">
                {source.flags.map((flag) => (
                  <span key={flag} className="verity-react-flag">
                    {flag}
                  </span>
                ))}
              </div>
            ) : null}
          </section>

          <div className="verity-react-expanded-grid">
            <div className="verity-react-expanded-grid__primary">
              <RubricSection axes={source.rubricAxes} />
              <FactGrid title="Verification details" items={source.verificationFacts} dense />
            </div>

            <div className="verity-react-expanded-grid__secondary">
              <FactGrid title="Source metadata" items={source.metadataFacts} />

              {source.topics?.length ? (
                <section className="verity-react-section">
                  <div className="verity-react-section__label">
                    <Glyph name="section" />
                    Topics
                  </div>
                  <div className="verity-react-topic-row">
                    {source.topics.map((topic) => (
                      <span key={topic} className="verity-react-topic-chip">
                        {topic}
                      </span>
                    ))}
                  </div>
                </section>
              ) : null}

              {source.fundersLabel ? (
                <section className="verity-react-section">
                  <div className="verity-react-section__label">
                    <Glyph name="section" />
                    Funding
                  </div>
                  <div className="verity-react-note-card">{source.fundersLabel}</div>
                </section>
              ) : null}
            </div>
          </div>

          <ContextDisclosure
            text={source.contextText}
            isOpen={isContextOpen}
            onToggle={(event) => {
              event.stopPropagation();
              onToggleContext();
            }}
          />
        </div>
      </div>
    </article>
  );
}

function ResultsList({ sources, resultKey }) {
  const [expandedId, setExpandedId] = useState(null);
  const [contextOpenId, setContextOpenId] = useState(null);

  useEffect(() => {
    setExpandedId(null);
    setContextOpenId(null);
  }, [resultKey]);

  return (
    <div className="verity-react-results">
      {sources.map((source) => {
        const isExpanded = expandedId === source.id;
        return (
          <SourceCard
            key={source.id}
            source={source}
            isExpanded={isExpanded}
            isContextOpen={contextOpenId === source.id}
            onToggleExpand={() => {
              setExpandedId((current) => (current === source.id ? null : source.id));
              setContextOpenId(null);
            }}
            onToggleContext={() => {
              setContextOpenId((current) => (current === source.id ? null : source.id));
            }}
          />
        );
      })}
    </div>
  );
}

export function InlineCardsApp({
  mode,
  progressText,
  sourceCount,
  sources,
  onCheck,
  onRetry,
  errorMessage,
  errorActionLabel,
  resultKey,
}) {
  const normalizedSources = useMemo(() => sources || [], [sources]);

  if (mode === "idle") {
    return <IdleCard sourceCount={sourceCount} onCheck={onCheck} />;
  }

  if (mode === "loading") {
    return <LoadingCard progressText={progressText} sourceCount={sourceCount} />;
  }

  if (mode === "error") {
    return (
      <ErrorCard
        message={errorMessage}
        actionLabel={errorActionLabel}
        onAction={onRetry}
      />
    );
  }

  if (!normalizedSources.length) {
    return (
      <div className="verity-react-panel">
        <div className="verity-react-panel__chrome" />
        <div className="verity-react-empty">No sources could be analyzed.</div>
      </div>
    );
  }

  return <ResultsList sources={normalizedSources} resultKey={resultKey} />;
}
