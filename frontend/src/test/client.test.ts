import { describe, expect, it } from "vitest";
import { ApiError, userMessageFor } from "../services/api/errors";
import { unwrapList } from "../services/api/client";

describe("api client helpers", () => {
  it("maps HTTP statuses to user-facing messages", () => {
    expect(userMessageFor(new ApiError("nope", { status: 401 }))).toMatch(/sign in/i);
    expect(userMessageFor(new ApiError("Invalid email or password.", { status: 401 }))).toMatch(
      /invalid email or password/i,
    );
    expect(userMessageFor(new ApiError("nope", { status: 403 }))).toMatch(/permission/i);
    expect(userMessageFor(new ApiError("missing", { status: 404 }))).toMatch(/missing/i);
    expect(userMessageFor(new ApiError("bad", { status: 422 }))).toMatch(/bad/i);
    expect(userMessageFor(new ApiError("boom", { status: 500 }))).toMatch(/server/i);
  });

  it("unwraps list envelopes", () => {
    expect(unwrapList([{ id: 1 }])).toEqual([{ id: 1 }]);
    expect(unwrapList({ items: [{ id: 2 }] })).toEqual([{ id: 2 }]);
    expect(unwrapList({ posts: [{ id: 3 }] }, ["posts"])).toEqual([{ id: 3 }]);
  });
});
