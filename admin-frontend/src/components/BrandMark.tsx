/**
 * The Pintell mark — the console's copy.
 *
 * Duplicated from the public site rather than shared, for the same reason the
 * two front ends duplicate their error helpers: they are separate deployables
 * with separate builds, and a shared module between them would be a package to
 * publish.
 *
 * **One drawing, both themes.** The brand ships as two files — a dark mark for
 * light backgrounds and a light one for dark backgrounds — and the obvious
 * wiring is two `<img>` tags swapped on the theme. This draws the same shape
 * as inline SVG filled with `currentColor` instead, which is the same result
 * with three properties the pair of files does not have: it inherits the theme
 * from CSS with nothing to keep in sync, it costs no request, and it stays
 * sharp at any size on any display.
 *
 * The geometry is a monogram of `p` and `i`: a sheared bowl with a counter cut
 * out of it, the dot of the `i` above right, and its stem-foot below left.
 * Every edge is straight except the two corners of the bowl, so the path is
 * exact rather than traced.
 */
export default function BrandMark({
  size = 28,
  title,
}: {
  size?: number | string;
  /** Set only where the mark stands alone; beside the wordmark it is decorative. */
  title?: string;
}) {
  return (
    <svg
      viewBox="0 0 100 100"
      width={size}
      height={size}
      fill="currentColor"
      role={title ? 'img' : 'presentation'}
      aria-hidden={title ? undefined : true}
      aria-label={title}
      focusable="false"
    >
      {title && <title>{title}</title>}
      {/* The drawing spans 15.6–90.6 across and 16–80.7 down, so its own centre
          is (53.1, 48.35) and not the (50, 50) of the box. The shift puts it
          back on centre, which matters because the mark is set on a filled
          tile: off-centre by three units reads as a crooked tile, not as a
          crooked mark. Kept as a transform so the path data stays the
          coordinates the shape was drawn in. */}
      <g transform="translate(-3.1 1.65)">
        {/* The dot of the `i` — detached, up and to the right. */}
        <path d="M77.4 16 H90.6 L84.1 28.6 H70.9 Z" />
        {/* The bowl. Outer contour and counter are one path under
            `fill-rule="evenodd"`, which cuts the hole out; a second shape in
            the theme's background colour would only work on the two
            backgrounds that colour was chosen for. */}
        <path
          fillRule="evenodd"
          d="M40.6 32 H85.4 L73.9 54.3 C71.1 59.8 65.4 63.3 59.2 63.3 H23.2 L34.7 41
             C37.5 35.5 43.2 32 49.4 32 Z
             M45.6 42.6 L40.1 53.2 H58.9 C61.3 53.2 63.5 51.9 64.6 49.7 L68.3 42.6 Z"
        />
        {/* The foot of the `i` stem — detached, down and to the left. */}
        <path d="M22.1 68.1 H35.3 L28.8 80.7 H15.6 Z" />
      </g>
    </svg>
  );
}
