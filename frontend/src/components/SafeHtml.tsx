import { createElement, type ReactNode } from 'react';

import { useI18n } from '../i18n';

/**
 * Renders notice HTML WITHOUT `dangerouslySetInnerHTML`.
 *
 * The backend already sanitises this markup with a strict allow-list before it
 * is stored. This component is the second, independent layer: the string is
 * parsed with DOMParser into an inert document (no scripts run, no resources
 * are fetched) and only nodes on the allow-list below are converted into React
 * elements. Attributes are dropped entirely, except `href` on links — and only
 * when it points at http/https/mailto. Anything else becomes plain text or
 * disappears.
 */

const ALLOWED_TAGS = new Set([
  'p', 'br', 'b', 'strong', 'i', 'em', 'u',
  'ul', 'ol', 'li',
  'div', 'span',
  'h1', 'h2', 'h3', 'h4',
  'a',
  'table', 'thead', 'tbody', 'tr', 'th', 'td',
  'blockquote', 'hr',
]);

/** Tags dropped together with their subtree. */
const DROPPED_SUBTREES = new Set([
  'script', 'style', 'iframe', 'object', 'embed', 'noscript', 'template', 'svg', 'math',
]);

const SAFE_PROTOCOLS = new Set(['http:', 'https:', 'mailto:']);

const VOID_TAGS = new Set(['br', 'hr']);

function safeHref(raw: string | null): string | undefined {
  if (!raw) return undefined;
  try {
    const url = new URL(raw, window.location.origin);
    return SAFE_PROTOCOLS.has(url.protocol) ? url.href : undefined;
  } catch {
    return undefined;
  }
}

function convert(node: Node, key: string): ReactNode {
  if (node.nodeType === Node.TEXT_NODE) {
    return node.textContent ?? '';
  }
  if (node.nodeType !== Node.ELEMENT_NODE) {
    return null; // comments, processing instructions, …
  }

  const element = node as Element;
  const tag = element.tagName.toLowerCase();

  if (DROPPED_SUBTREES.has(tag)) return null;

  const children = Array.from(element.childNodes)
    .map((child, index) => convert(child, `${key}.${index}`))
    .filter((child) => child !== null && child !== '');

  if (!ALLOWED_TAGS.has(tag)) {
    // Unknown element: keep its readable content, discard the element itself.
    return children.length ? <span key={key}>{children}</span> : null;
  }

  if (VOID_TAGS.has(tag)) {
    return createElement(tag, { key });
  }

  if (tag === 'a') {
    const href = safeHref(element.getAttribute('href'));
    if (!href) return <span key={key}>{children}</span>;
    return (
      <a key={key} href={href} target="_blank" rel="noopener noreferrer nofollow">
        {children}
      </a>
    );
  }

  return createElement(tag, { key }, children.length ? children : undefined);
}

interface SafeHtmlProps {
  html: string;
  className?: string;
}

export default function SafeHtml({ html, className }: SafeHtmlProps) {
  const { t } = useI18n();

  if (!html?.trim()) {
    // Only the "nothing here" placeholder is translated. The notice body is
    // upstream content, published in whatever language the borrower used.
    return <p className="muted">{t('notice.noText')}</p>;
  }

  // DOMParser builds an inert document: nothing here executes or loads.
  const parsed = new DOMParser().parseFromString(html, 'text/html');
  const nodes = Array.from(parsed.body.childNodes)
    .map((node, index) => convert(node, `n${index}`))
    .filter((node) => node !== null && node !== '');

  return <div className={className}>{nodes}</div>;
}
