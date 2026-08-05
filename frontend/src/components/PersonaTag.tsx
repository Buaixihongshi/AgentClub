export function PersonaTag({ label }: { label?: string }) {
  if (!label) {
    return null;
  }
  return <span className="persona-tag">persona: {label}</span>;
}
