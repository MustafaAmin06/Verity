# Verity Privacy Policy

**Last updated:** April 14, 2026

## What Verity Does

Verity is a Chrome extension that verifies sources cited in ChatGPT responses. It checks whether linked URLs are live, accessible, and support the claims made by the AI.

## What Data We Collect

When you trigger a source check, Verity reads the following from the active ChatGPT page:

- **Cited URLs** — the links referenced in the AI response
- **AI response text** — the relevant portion of ChatGPT's reply containing the citations
- **Your prompt** — the question you asked ChatGPT (used to assess source relevance)

This data is sent to our backend server for processing.

## What We Do NOT Collect

- No names, email addresses, or personally identifiable information
- No browsing history or activity outside of ChatGPT
- No financial, health, or authentication data
- No cookies or tracking identifiers
- No keystroke or mouse activity logging

## How Data Is Processed

Extracted URLs are sent to our hosted backend server, currently operated on DigitalOcean App Platform, which:

1. Fetches each cited URL to check if it is live
2. Analyzes whether the source content supports the AI's claims
3. Cross-references academic metadata via the OpenAlex API
4. Returns a verification score to the extension

## Data Retention

- Source verification results may be cached temporarily on the server to avoid redundant processing
- By default, user prompts and full AI responses are processed in memory and are not retained in server-side diagnostics
- Optional developer-only triage capture is disabled by default in production
- Operational logs may temporarily contain requested source URLs and high-level processing metadata, but not prompts or full AI responses by default

## Data Sharing

- We do not sell, share, or transfer user data to any third party
- Cited URLs may be fetched by our server (to verify they are live), which means the target website will see a request from our server
- Academic metadata lookups are made to the public OpenAlex API

## Your Choices

- Verity only runs when you trigger a source check (or enable auto-check in settings)
- You can disable the extension at any time from the popup or Chrome's extension settings
- No account or sign-up is required

## Changes to This Policy

If we update this policy, the changes will be reflected on this page with an updated date.

## Contact

For questions about this privacy policy, open an issue at the project's GitHub repository.
