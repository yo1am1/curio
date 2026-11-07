import { Link } from "react-router";

import { PageHeader } from "./PageHeader";

export function NotFound() {
  return (
    <div style={{ display: "grid", gap: "1rem" }}>
      <PageHeader title="Nothing here." intro="This page does not exist in Curio." />
      <p>
        <Link to="/">Back to Curio</Link>
      </p>
    </div>
  );
}
