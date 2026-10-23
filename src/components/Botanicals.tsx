// Decorative ink-outlined flowers and leaves in the style of a herbarium illustration. Purely visual.
const ink = "var(--ink)";

function Flower({
  x,
  y,
  r,
  petals,
  fill,
  center = "var(--marigold)",
  rotate = 0,
}: {
  x: number;
  y: number;
  r: number;
  petals: number;
  fill: string;
  center?: string;
  rotate?: number;
}) {
  return (
    <g
      transform={`translate(${x} ${y}) rotate(${rotate})`}
      stroke={ink}
      strokeWidth={2.5}
      strokeLinejoin="round"
    >
      {Array.from({ length: petals }, (_, i) => (
        <ellipse
          key={i}
          cx={0}
          cy={-r * 0.62}
          rx={r * 0.32}
          ry={r * 0.55}
          fill={fill}
          transform={`rotate(${(360 / petals) * i})`}
        />
      ))}
      <circle r={r * 0.3} fill={center} />
      <circle r={r * 0.08} cx={-r * 0.08} cy={-r * 0.06} fill={ink} stroke="none" />
    </g>
  );
}

function Leaf({
  x,
  y,
  len,
  rotate,
  fill = "var(--leaf)",
}: {
  x: number;
  y: number;
  len: number;
  rotate: number;
  fill?: string;
}) {
  const w = len * 0.38;
  return (
    <g
      transform={`translate(${x} ${y}) rotate(${rotate})`}
      stroke={ink}
      strokeWidth={2.5}
      strokeLinejoin="round"
      strokeLinecap="round"
    >
      <path
        d={`M0 0 C ${w} ${-len * 0.25}, ${w} ${-len * 0.75}, 0 ${-len} C ${-w} ${-len * 0.75}, ${-w} ${-len * 0.25}, 0 0 Z`}
        fill={fill}
      />
      <path d={`M0 ${-len * 0.05} L0 ${-len * 0.9}`} fill="none" strokeWidth={1.8} />
      {[0.3, 0.5, 0.7].map((t) => (
        <path
          key={t}
          d={`M0 ${-len * t} l${w * 0.55} ${-len * 0.1} M0 ${-len * t} l${-w * 0.55} ${-len * 0.1}`}
          fill="none"
          strokeWidth={1.5}
        />
      ))}
    </g>
  );
}

function Stem({ d }: { d: string }) {
  return <path d={d} fill="none" stroke={ink} strokeWidth={2.5} strokeLinecap="round" />;
}

// A cluster of stems, leaves and flowers. `variant` mirrors and recolours it so two corners don't look identical.
export function Botanicals({
  className = "",
  variant = "a",
}: {
  className?: string;
  variant?: "a" | "b";
}) {
  const b = variant === "b";
  return (
    <svg
      viewBox="0 0 260 260"
      aria-hidden="true"
      focusable="false"
      className={`pointer-events-none select-none ${className}`}
      style={b ? { transform: "scaleX(-1)" } : undefined}
    >
      <Stem d="M40 255 C 60 200, 70 150, 95 95" />
      <Stem d="M120 255 C 125 200, 150 170, 175 130" />
      <Stem d="M80 255 C 85 220, 40 190, 30 160" />
      <Leaf x={70} y={210} len={70} rotate={-35} />
      <Leaf x={128} y={215} len={60} rotate={40} fill="oklch(0.66 0.12 140)" />
      <Leaf x={55} y={185} len={50} rotate={-80} />
      <Leaf x={150} y={175} len={45} rotate={70} />
      <Flower x={95} y={90} r={38} petals={12} fill={b ? "var(--periwinkle)" : "var(--poppy)"} />
      <Flower
        x={178}
        y={124}
        r={28}
        petals={5}
        fill={b ? "var(--marigold)" : "var(--periwinkle)"}
        center={b ? "var(--poppy)" : "var(--marigold)"}
        rotate={18}
      />
      <Flower x={30} y={155} r={22} petals={6} fill="var(--blossom)" rotate={-10} />
    </svg>
  );
}
