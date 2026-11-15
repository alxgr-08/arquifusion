import { describe, expect, it } from "vitest";
import { formatDateTime, formatTime } from "./domain";

describe("date formatting", () => {
  it("shows a dash when the timestamp is missing or invalid", () => {
    expect(formatTime(undefined)).toBe("—");
    expect(formatTime("not-a-date")).toBe("—");
    expect(formatDateTime("")).toBe("—");
  });

  it("formats a valid UTC timestamp", () => {
    expect(formatTime("2026-11-15T12:05:00.000000Z")).toMatch(/\d{2}:\d{2}/);
  });
});
