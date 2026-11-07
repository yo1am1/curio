/** Options matching `query`: prefix matches first, then matches anywhere (case-insensitive). */
export function filterOptions<T>(
  options: readonly T[],
  query: string,
  label: (option: T) => string,
): T[] {
  const needle = query.trim().toLocaleLowerCase();
  if (!needle) return [...options];
  const starts: T[] = [];
  const contains: T[] = [];
  for (const option of options) {
    const text = label(option).toLocaleLowerCase();
    if (text.startsWith(needle)) starts.push(option);
    else if (text.includes(needle)) contains.push(option);
  }
  return [...starts, ...contains];
}
