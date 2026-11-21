import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { SourceSchemaPanel } from "./SourceSchemaPanel";

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
const draft = () => ({ version: 1, source: "synthetic", payer: null, network: null, source_version: null,
  contract: "payer-network-roster/1", grain: "source_declared", headers: ["NPI", "Site", "Directory Flag"],
  mapping: {}, transforms: {}, required: [], required_when: [], ignored_fields: [], evidence_refs: [], source_notes: {}, settings: { delimiter: ",", encoding: "utf-8-sig" } });

test("analyst previews flag meaning, saves a proposal and separately records a review", async () => {
  let reviewed = false, proposed: ReturnType<typeof draft> | null = null;
  const calls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, options: RequestInit) => {
    const body = JSON.parse(String(options.body));
    calls.push(url);
    if (url.endsWith("/inspect") && !url.includes("/proposals/")) return new Response(JSON.stringify({ inspection: { fields: ["NPI", "Site", "Directory Flag"], row_count: 1 }, recognition: { status: reviewed ? "exact" : "unknown" }, draft: reviewed ? proposed : draft(), catalog_revision: reviewed ? 1 : 0, fields: ["practitioner_npi", "location_id", "directory_visible"], pending: [] }));
    if (url.endsWith("/preview")) {
      expect(body.definition.mapping).toEqual({ practitioner_npi: "NPI", location_id: "Site", directory_visible: "Directory Flag" });
      expect(body.definition.transforms.directory_visible[0].values).toEqual({ N: true, Y: false });
      expect(body.definition.source_notes["analyst-source-note"]).toContain("Y excludes");
      return new Response(JSON.stringify({ preview_token: "current-preview", report: { coverage: "complete_for_declared_scope", row_count: 1, rows_truncated: false, samples_clipped: false, rows: [{ locator: "$.records[0]", normalized: { practitioner_npi: "1000000004", location_id: "NORTH", directory_visible: false } }], findings: { counts: { network_scope_unresolved: 1 }, total: 1 }, eligibility: { state: "held" } } }));
    }
    if (url.endsWith("/proposals")) { proposed = body.definition; expect(body.preview_token).toBe("current-preview"); return new Response(JSON.stringify({ id: "proposal-one" })); }
    if (url.endsWith("/review")) { reviewed = true; expect(body.actor).toBe("Synthetic reviewer"); expect(body.preview_token).toBe("current-preview"); return new Response(JSON.stringify({ revision: 1 })); }
    throw new Error(`Unexpected request ${url}`);
  }));
  render(<SourceSchemaPanel receiptId="b" attemptId="attempt-b" />);
  fireEvent.click(screen.getByRole("button", { name: "Source format catalog" }));
  await screen.findByText(/Recognition:/);
  fireEvent.change(screen.getByLabelText("Row scope"), { target: { value: "practitioner_office" } });
  fireEvent.change(screen.getByRole("combobox", { name: "Source column for Practitioner NPI" }), { target: { value: "NPI" } });
  fireEvent.change(screen.getByRole("combobox", { name: "Source column for Office identifier" }), { target: { value: "Site" } });
  fireEvent.change(screen.getByRole("combobox", { name: "Source column for Directory visibility" }), { target: { value: "Directory Flag" } });
  fireEvent.change(screen.getByLabelText("Exact source value meaning “display”"), { target: { value: "N" } });
  fireEvent.change(screen.getByLabelText("Exact source value meaning “exclude”"), { target: { value: "Y" } });
  fireEvent.change(screen.getByLabelText("Source note supporting these meanings"), { target: { value: "NPI is practitioner; Site is office key; Y excludes and N displays." } });
  fireEvent.click(screen.getByRole("button", { name: "Preview source interpretation" }));
  await screen.findByText("false");
  expect(screen.getByText(/No dataset acceptance has occurred/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Save format proposal" }));
  await screen.findByRole("button", { name: "Record reviewed interpretation" });
  expect(screen.getByRole("button", { name: "Record reviewed interpretation" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Reviewer"), { target: { value: "Synthetic reviewer" } });
  fireEvent.change(screen.getByLabelText("Review reason"), { target: { value: "Checked source instructions and preview" } });
  fireEvent.click(screen.getByRole("button", { name: "Record reviewed interpretation" }));
  await screen.findByText(/Reviewed source interpretation recorded/);
  expect(reviewed).toBe(true);
  expect(calls.filter(path => path.endsWith("/review"))).toHaveLength(1);
});

test("stale server response leaves an actionable message and never records a review", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "Host interpretation changed; reopen the current attempt" }), { status: 409 })));
  render(<SourceSchemaPanel receiptId="b" attemptId="old" />);
  fireEvent.click(screen.getByRole("button", { name: "Source format catalog" }));
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Host interpretation changed"));
  expect(screen.queryByRole("button", { name: "Record reviewed interpretation" })).not.toBeInTheDocument();
});
