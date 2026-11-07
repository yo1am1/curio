import { useHealth } from "../../api/queries";

export function useBooksIntro(): string {
  const count = useHealth().data?.book_count;
  return count
    ? `Search ${count.toLocaleString("en-US")} popular books, start from one you loved, or browse by mood.`
    : "Search popular books, start from one you loved, or browse by mood.";
}
