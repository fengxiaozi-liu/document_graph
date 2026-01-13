export function gradientForId(id: string): string {
  const n = Array.from(id).reduce((acc, ch) => acc + ch.charCodeAt(0), 0);
  const palettes = [
    ["from-indigo-500/30", "via-sky-500/20", "to-emerald-500/30"],
    ["from-fuchsia-500/25", "via-rose-500/20", "to-amber-500/25"],
    ["from-cyan-500/25", "via-violet-500/20", "to-lime-500/25"],
    ["from-amber-500/25", "via-orange-500/20", "to-pink-500/25"],
    ["from-slate-500/25", "via-blue-500/20", "to-teal-500/25"],
  ];
  const p = palettes[n % palettes.length];
  return `bg-gradient-to-br ${p.join(" ")}`;
}

