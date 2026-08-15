import { useMemo, useState } from 'react';

import type { YearCount } from '../api/types';
import { useI18n } from '../i18n';

/**
 * Notices per publication year — one series, so one hue and no legend
 * (the title names the series). Bars are anchored to the baseline with
 * rounded data-ends, separated by a surface gap, over a recessive grid.
 * Only the tallest bar is labelled directly; the rest are read on hover,
 * and a visually-hidden table carries the same numbers for screen readers.
 */

const WIDTH = 780;
const HEIGHT = 260;
const PADDING = { top: 16, right: 12, bottom: 30, left: 48 };
const BAR_GAP = 2; // surface gap between adjacent bars
const MAX_BAR_WIDTH = 44;
const CORNER = 4;

interface Tooltip {
  x: number;
  y: number;
  year: number;
  count: number;
}

function niceTicks(max: number, count = 4): number[] {
  if (max <= 0) return [0];
  const rawStep = max / count;
  const magnitude = 10 ** Math.floor(Math.log10(rawStep));
  const step =
    [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((candidate) => candidate >= rawStep) ??
    magnitude * 10;
  const ticks: number[] = [];
  for (let value = 0; value <= max + step / 2; value += step) ticks.push(value);
  return ticks;
}

function compact(value: number): string {
  if (value >= 1000) return `${(value / 1000).toFixed(value >= 10000 ? 0 : 1)}k`;
  return `${value}`;
}

export default function YearBarChart({ data }: { data: YearCount[] }) {
  const { t, formatNumber } = useI18n();
  const [tooltip, setTooltip] = useState<Tooltip | null>(null);

  const geometry = useMemo(() => {
    const plotWidth = WIDTH - PADDING.left - PADDING.right;
    const plotHeight = HEIGHT - PADDING.top - PADDING.bottom;
    const maxCount = Math.max(1, ...data.map((row) => row.count));
    const ticks = niceTicks(maxCount);
    const scaleMax = ticks[ticks.length - 1] || maxCount;
    const band = data.length ? plotWidth / data.length : plotWidth;
    const barWidth = Math.min(MAX_BAR_WIDTH, Math.max(2, band - BAR_GAP));
    const peak = data.reduce<YearCount | null>(
      (best, row) => (!best || row.count > best.count ? row : best),
      null,
    );

    const bars = data.map((row, index) => {
      const height = (row.count / scaleMax) * plotHeight;
      return {
        ...row,
        x: PADDING.left + index * band + (band - barWidth) / 2,
        y: PADDING.top + plotHeight - height,
        width: barWidth,
        height: Math.max(height, row.count > 0 ? 2 : 0),
        isPeak: peak?.year === row.year,
      };
    });

    return { plotWidth, plotHeight, ticks, scaleMax, bars, band };
  }, [data]);

  if (data.length === 0) {
    return <p className="muted">{t('chart.noDated')}</p>;
  }

  // With many years, label every other tick so they never collide.
  const labelEvery = geometry.band < 34 ? 2 : 1;

  return (
    <figure className="chart-figure">
      <div className="chart-canvas">
        <svg
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          className="year-chart"
          role="img"
          aria-label={t('chart.title')}
          onMouseLeave={() => setTooltip(null)}
        >
          {/* Recessive grid + value axis */}
          {geometry.ticks.map((tick) => {
            const y =
              PADDING.top + geometry.plotHeight - (tick / geometry.scaleMax) * geometry.plotHeight;
            return (
              <g key={tick}>
                <line
                  x1={PADDING.left}
                  x2={WIDTH - PADDING.right}
                  y1={y}
                  y2={y}
                  className="chart-grid"
                />
                <text x={PADDING.left - 10} y={y + 4} className="chart-axis-label" textAnchor="end">
                  {compact(tick)}
                </text>
              </g>
            );
          })}

          {geometry.bars.map((bar) => (
            <g key={bar.year}>
              {/* Full-height hit target: easier to hover than a short bar. */}
              <rect
                x={bar.x - BAR_GAP / 2}
                y={PADDING.top}
                width={bar.width + BAR_GAP}
                height={geometry.plotHeight}
                fill="transparent"
                onMouseEnter={() =>
                  setTooltip({
                    x: bar.x + bar.width / 2,
                    y: bar.y,
                    year: bar.year,
                    count: bar.count,
                  })
                }
              />
              <rect
                x={bar.x}
                y={bar.y}
                width={bar.width}
                height={bar.height}
                rx={Math.min(CORNER, bar.width / 2)}
                className={`chart-bar ${tooltip?.year === bar.year ? 'hovered' : ''}`}
              />
              {bar.isPeak && (
                <text
                  x={bar.x + bar.width / 2}
                  y={bar.y - 6}
                  className="chart-value-label"
                  textAnchor="middle"
                >
                  {compact(bar.count)}
                </text>
              )}
            </g>
          ))}

          {/* Category axis */}
          <line
            x1={PADDING.left}
            x2={WIDTH - PADDING.right}
            y1={PADDING.top + geometry.plotHeight}
            y2={PADDING.top + geometry.plotHeight}
            className="chart-axis"
          />
          {geometry.bars.map((bar, index) =>
            index % labelEvery === 0 ? (
              <text
                key={`label-${bar.year}`}
                x={bar.x + bar.width / 2}
                y={HEIGHT - 10}
                className="chart-axis-label"
                textAnchor="middle"
              >
                {bar.year}
              </text>
            ) : null,
          )}
        </svg>

        {tooltip && (
          <div
            className="chart-tooltip"
            style={{
              left: `${(tooltip.x / WIDTH) * 100}%`,
              top: `${(tooltip.y / HEIGHT) * 100}%`,
            }}
            role="presentation"
          >
            <strong>{tooltip.year}</strong>
            <span>{t('chart.tooltip', { count: tooltip.count })}</span>
          </div>
        )}
      </div>

      {/* Same numbers, available to assistive tech and to copy/paste. */}
      <table className="sr-only">
        <caption>{t('chart.title')}</caption>
        <thead>
          <tr>
            <th scope="col">{t('chart.year')}</th>
            <th scope="col">{t('chart.notices')}</th>
          </tr>
        </thead>
        <tbody>
          {data.map((row) => (
            <tr key={row.year}>
              <td>{row.year}</td>
              <td>{formatNumber(row.count)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}
