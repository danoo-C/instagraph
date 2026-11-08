import { describe, expect, it } from "vitest";
import { escapeHtml } from "@/lib/utils";

describe("escapeHtml", () => {
  it("escapes markup characters", () => {
    expect(escapeHtml(`<b a="1">'x'&`)).toBe("&#60;b a=&#34;1&#34;&#62;&#39;x&#39;&#38;");
    expect(escapeHtml(`<img src=x onerror=alert(1)>`)).not.toContain("<");
  });
  it("handles emoji, numbers and nullish values", () => {
    expect(escapeHtml("😂 ok")).toBe("😂 ok");
    expect(escapeHtml(3)).toBe("3");
    expect(escapeHtml(null)).toBe("");
    expect(escapeHtml(undefined)).toBe("");
  });
});
