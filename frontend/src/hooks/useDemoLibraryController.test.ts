import { describe, expect, it } from "vitest";

import {
  EMPTY_DEMO_LIBRARY_FILTERS,
  buildDemoLibraryFilterParams,
  hasActiveDemoLibraryFilters,
} from "./useDemoLibraryController";

describe("demo library filters", () => {
  it("normalizes supported text and numeric filters", () => {
    const params = buildDemoLibraryFilterParams({
      ...EMPTY_DEMO_LIBRARY_FILTERS,
      mapName: " de_mirage ",
      status: "loaded",
      playerQuery: " donk ",
      minKills: "12",
      maxDeaths: "-1",
      minKd: "1.25",
      roundsMin: "abc",
    });

    expect(params).toMatchObject({
      map_name: "de_mirage",
      status: "loaded",
      player_query: "donk",
      min_kills: 12,
      min_kd: 1.25,
    });
    expect(params).not.toHaveProperty("max_deaths");
    expect(params).not.toHaveProperty("rounds_min");
  });

  it("reports whether any advanced filter is active", () => {
    expect(hasActiveDemoLibraryFilters({ ...EMPTY_DEMO_LIBRARY_FILTERS })).toBe(false);
    expect(
      hasActiveDemoLibraryFilters({ ...EMPTY_DEMO_LIBRARY_FILTERS, status: "pending" }),
    ).toBe(true);
    expect(
      hasActiveDemoLibraryFilters({ ...EMPTY_DEMO_LIBRARY_FILTERS, durationMax: "30" }),
    ).toBe(true);
  });
});
