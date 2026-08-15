import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { useLocation } from 'react-router-dom';

import type { SearchResult } from '../api/types';

/**
 * Where an open citation lives, and why it is not page state.
 *
 * The pane is not a dialog over the page — it takes a **column beside it**, so
 * the reader compares the claim and the source without one covering the other.
 * That layout cannot be decided by the component that asks for it: a claim in
 * the chat, a row in the similar-awards block and a hit in the search list all
 * open the same pane, and each of them sits several levels inside `main`. A
 * panel opened from there can only overlay, because it has no way to make the
 * shell narrower.
 *
 * So the shell owns it. A page calls `useCitation().open(result)`, the layout
 * puts the reading column and the pane side by side, and the header and footer
 * keep their full width because nothing is drawn on top of them.
 */
type CitationState = {
  result: SearchResult | null;
  open: (result: SearchResult) => void;
  close: () => void;
  /** The width the reader dragged the pane to, or null for the CSS default. */
  width: number | null;
  resize: (width: number | null) => void;
};

const Context = createContext<CitationState>({
  result: null,
  open: () => undefined,
  close: () => undefined,
  width: null,
  resize: () => undefined,
});

export function CitationProvider({ children }: { children: ReactNode }) {
  const [result, setResult] = useState<SearchResult | null>(null);
  const [width, setWidth] = useState<number | null>(null);
  const { pathname } = useLocation();

  const open = useCallback((next: SearchResult) => setResult(next), []);
  const close = useCallback(() => setResult(null), []);

  /**
   * **Leaving the page closes the source.**
   *
   * The pane is a column of *this* page — it was opened to be read against a
   * claim on it — so on the next page it is a slab of an unrelated document
   * that the reader did not ask for, holding half the window until they find
   * the ×. Worse, it takes that width from a page laid out for it: the notice
   * detail stays in its re-laid branch (D52/D53) because a source is open
   * beside it, and nothing on screen says why.
   *
   * Keyed on the path alone, not on `location.key`: the search page writes its
   * query into the URL, and closing the source every time somebody types would
   * be the same fault from the other side.
   *
   * The dragged width deliberately survives — that is a preference about the
   * split, not about the document that happened to be in it.
   */
  useEffect(() => {
    setResult(null);
  }, [pathname]);

  const resize = useCallback((next: number | null) => setWidth(next), []);

  const value = useMemo(
    () => ({ result, open, close, width, resize }),
    [result, open, close, width, resize],
  );
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

/**
 * The open citation, and the controls for it.
 *
 * `open` is what every citation badge in the product calls. Handing it around
 * as a callback prop is the alternative, and it means every component between
 * the badge and the shell carries a prop it does not use.
 */
export function useCitation(): CitationState {
  return useContext(Context);
}
